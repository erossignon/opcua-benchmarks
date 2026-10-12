// SPDX-License-Identifier: AGPL-3.0-or-later
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.
//
//    Copyright 2026 (c) o6 Automation GmbH (Author: Daniel Opitz)

// Address-space helpers derived from server.c, Copyright 2026 Sterfive.

/* S2OPC uses change-triggered notifications, so counters advance through local
 * writes at the requested sampling period, skipping missed grid slots. */
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <poll.h>
#include <stdatomic.h>

#include "libs2opc_common_config.h"
#include "libs2opc_server.h"
#include "libs2opc_server_config.h"
#include "libs2opc_server_config_custom.h"
#include "libs2opc_request_builder.h"
#include "sopc_address_space.h"
#include "sopc_crypto_profiles.h"
#include "sopc_encodeabletype.h"
#include "sopc_mem_alloc.h"
#include "sopc_pki_stack.h"
#include "sopc_date_time.h"
#include "sopc_toolkit_build_info.h"
#include "sopc_uanodeset_loader.h"
#include "sopc_user_manager.h"

#define APPLICATION_URI "urn:o6:benchmark:counter"

static atomic_bool stopped;
static void on_stopped(SOPC_ReturnStatus status) {
    (void)status;
    stopped = true;
}

static FILE *output;
static unsigned port, count, sampling_ms;
static bool measure_server;
static uint64_t start_ns, end_ns, measured_start_ns, measured_end_ns;
static bool recording_enabled;
static uint64_t interval_ns, *interval_counts;
static unsigned interval_count;

static void record_progress(uint64_t timestamp, uint64_t delta) {
    interval_counts[(timestamp - measured_start_ns) / interval_ns] += delta;
}

static void write_intervals(void) {
    fputs(",\"interval_counts\":[", output);
    for(unsigned i = 0; i < interval_count; ++i)
        fprintf(output, "%s%" PRIu64, i ? "," : "", interval_counts[i]);
    fputc(']', output);
}

static uint64_t now_ns(void) {
    struct timespec value;
    if(clock_gettime(CLOCK_MONOTONIC, &value)) abort();
    return (uint64_t)value.tv_sec * 1000000000 + (uint64_t)value.tv_nsec;
}

static void arguments(int argc, char **argv) {
    if(argc != 5) exit(2);
    port = (unsigned)strtoul(argv[1], NULL, 10);
    count = (unsigned)strtoul(argv[2], NULL, 10);
    sampling_ms = (unsigned)strtoul(argv[3], NULL, 10);
    if(!port || port > 65535 || !count || count > 1048576 || !sampling_ms || sampling_ms > 100) exit(2);
    if(strcmp(argv[4], "server") && strcmp(argv[4], "client")) exit(2);
    measure_server = !strcmp(argv[4], "server");
    output = fdopen(dup(STDOUT_FILENO), "w");
    if(!output || dup2(STDERR_FILENO, STDOUT_FILENO) < 0) exit(2);
    setvbuf(output, NULL, _IOLBF, 0);
    setvbuf(stdin, NULL, _IONBF, 0);
}

/* Controls are short, newline-terminated coordinator writes. */
static int control(void) {
    struct pollfd input = {STDIN_FILENO, POLLIN, 0};
    if(poll(&input, 1, 0) <= 0) return 0;
    char line[128];
    if(!fgets(line, sizeof(line), stdin) || !strcmp(line, "quit\n")) return -1;
    uint64_t begin, finish, measured_begin, measured_finish, period;
    if(sscanf(line, "arm %" SCNu64 " %" SCNu64 " %" SCNu64 " %" SCNu64 " %" SCNu64,
              &begin, &finish, &measured_begin, &measured_finish, &period) != 5 ||
       start_ns || begin <= now_ns() || measured_begin < begin || measured_finish <= measured_begin ||
       finish < measured_finish || !period || (measured_finish - measured_begin) % period ||
       (measured_finish - measured_begin) / period > 1000) exit(2);
    start_ns = begin;
    end_ns = finish;
    measured_start_ns = measured_begin;
    measured_end_ns = measured_finish;
    interval_ns = period;
    interval_count = (unsigned)((measured_finish - measured_begin) / period);
    if(recording_enabled) {
        interval_counts = (uint64_t *)calloc(interval_count, sizeof(*interval_counts));
        if(!interval_counts) exit(2);
    }
    fprintf(output, "{\"event\":\"armed\",\"start_ns\":%" PRIu64 ",\"end_ns\":%" PRIu64
            ",\"measured_start_ns\":%" PRIu64 ",\"measured_end_ns\":%" PRIu64 ",\"interval_ns\":%" PRIu64 "}\n",
            start_ns, end_ns, measured_start_ns, measured_end_ns, interval_ns);
    return 1;
}

/* Fill a String array variable that S2OPC's base NodeSet leaves empty. */
static SOPC_ReturnStatus set_string_array(SOPC_AddressSpace *space, uint32_t id,
                                          const char **values, int32_t count) {
    SOPC_NodeId node_id = {.IdentifierType = SOPC_IdentifierType_Numeric, .Namespace = 0, .Data.Numeric = id};
    bool found = false;
    SOPC_AddressSpace_Node *node = SOPC_AddressSpace_Get_Node(space, &node_id, &found);
    SOPC_Variant *value;
    int32_t index;
    if(!found || !node)
        return SOPC_STATUS_NOK;
    value = SOPC_AddressSpace_Get_Value(space, node);
    SOPC_Variant_Clear(value);
    value->BuiltInTypeId = SOPC_String_Id;
    value->ArrayType = SOPC_VariantArrayType_Array;
    value->Value.Array.Content.StringArr = SOPC_Calloc((size_t)count, sizeof(SOPC_String));
    if(!value->Value.Array.Content.StringArr)
        return SOPC_STATUS_OUT_OF_MEMORY;
    value->Value.Array.Length = count;
    for(index = 0; index < count; ++index) {
        SOPC_String_Initialize(&value->Value.Array.Content.StringArr[index]);
        if(SOPC_String_CopyFromCString(&value->Value.Array.Content.StringArr[index], values[index]) != SOPC_STATUS_OK)
            return SOPC_STATUS_OUT_OF_MEMORY;
    }
    return SOPC_STATUS_OK;
}

static SOPC_ReturnStatus add_counter(SOPC_AddressSpace *space, uint32_t id, const char *name) {
    SOPC_AddressSpace_Node *node = SOPC_Calloc(1, sizeof(SOPC_AddressSpace_Node));
    OpcUa_VariableNode *variable;
    OpcUa_ReferenceNode *references;
    SOPC_ReturnStatus status = SOPC_STATUS_OK;
    if(!node)
        return SOPC_STATUS_OUT_OF_MEMORY;
    SOPC_AddressSpace_Node_Initialize(space, node, OpcUa_NodeClass_Variable);
    node->value_status = SOPC_GoodGenericStatus;
    variable = &node->data.variable;
    variable->NodeClass = OpcUa_NodeClass_Variable;
    variable->NodeId.IdentifierType = SOPC_IdentifierType_Numeric;
    variable->NodeId.Namespace = 1;
    variable->NodeId.Data.Numeric = id;
    variable->BrowseName.NamespaceIndex = 1;
    status = SOPC_String_CopyFromCString(&variable->BrowseName.Name, name);
    if(status == SOPC_STATUS_OK)
        status = SOPC_String_CopyFromCString(&variable->DisplayName.defaultText, name);
    variable->DataType.IdentifierType = SOPC_IdentifierType_Numeric;
    variable->DataType.Namespace = 0;
    variable->DataType.Data.Numeric = SOPC_UInt64_Id;
    variable->AccessLevel = 0x03; /* CurrentRead | CurrentWrite */
    variable->UserAccessLevel = 0x03;
    variable->ValueRank = -1;
    references = SOPC_Calloc(2, sizeof(OpcUa_ReferenceNode));
    if(!references)
        status = SOPC_STATUS_OUT_OF_MEMORY;
    if(status == SOPC_STATUS_OK) {
        OpcUa_ReferenceNode_Initialize(&references[0]);
        OpcUa_ReferenceNode_Initialize(&references[1]);
        references[0].ReferenceTypeId.Data.Numeric = 40; /* HasTypeDefinition */
        references[0].TargetId.NodeId.Data.Numeric = 63; /* BaseDataVariableType */
        references[1].ReferenceTypeId.Data.Numeric = 35; /* Organizes, from ObjectsFolder */
        references[1].IsInverse = true;
        references[1].TargetId.NodeId.Data.Numeric = 85;
        variable->NoOfReferences = 2;
        variable->References = references;
        variable->Value.BuiltInTypeId = SOPC_UInt64_Id;
        variable->Value.ArrayType = SOPC_VariantArrayType_SingleValue;
        variable->Value.Value.Uint64 = 0;
        status = SOPC_AddressSpace_Append(space, node);
    }
    if(status != SOPC_STATUS_OK) {
        if(!variable->References) SOPC_Free(references);
        SOPC_AddressSpace_Node_Delete(space, node);
    }
    return status;
}

static SOPC_ReturnStatus address_space(const char *nodeset) {
    const char *namespaces[] = {"http://opcfoundation.org/UA/", APPLICATION_URI};
    const char *servers[] = {APPLICATION_URI};
    FILE *file = fopen(nodeset, "r");
    if(!file) return SOPC_STATUS_NOK;
    SOPC_AddressSpace *space = SOPC_UANodeSet_Parse(file);
    fclose(file);
    if(!space) return SOPC_STATUS_NOK;
    SOPC_ReturnStatus status = set_string_array(space, 2255, namespaces, 2);
    if(status == SOPC_STATUS_OK) status = set_string_array(space, 2254, servers, 1);
    for(unsigned i = 0; status == SOPC_STATUS_OK && i < count; ++i) {
        char name[32];
        snprintf(name, sizeof(name), "Counter%u", i);
        status = add_counter(space, i + 1, name);
    }
    if(status == SOPC_STATUS_OK) status = SOPC_ServerConfigHelper_SetAddressSpace(space);
    if(status != SOPC_STATUS_OK) SOPC_AddressSpace_Delete(space);
    return status;
}

static SOPC_ReturnStatus start_server(const char *nodeset) {
    SOPC_Log_Configuration logs = SOPC_Common_GetDefaultLogConfiguration();
    logs.logLevel = SOPC_LOG_LEVEL_ERROR;
    logs.logSystem = SOPC_LOG_SYSTEM_NO_LOG;
    SOPC_ReturnStatus status = SOPC_CommonHelper_Initialize(&logs, NULL);
    if(status == SOPC_STATUS_OK) status = SOPC_ServerConfigHelper_Initialize();
    if(status == SOPC_STATUS_OK) status = SOPC_ServerConfigHelper_SetShutdownCountdown(0);
    if(status == SOPC_STATUS_OK)
        status = SOPC_ServerConfigHelper_SetApplicationDescription(
            APPLICATION_URI, "urn:o6:benchmark", "S2OPC counter server", NULL, OpcUa_ApplicationType_Server);
    char url[64];
    snprintf(url, sizeof(url), "opc.tcp://127.0.0.1:%u", port);
    SOPC_Endpoint_Config *endpoint = status == SOPC_STATUS_OK ?
        SOPC_ServerConfigHelper_CreateEndpoint(url, true) : NULL;
    SOPC_SecurityConfig *policy = endpoint ?
        SOPC_EndpointConfig_AddSecurityConfig(endpoint, SOPC_SecurityPolicy_None) : NULL;
    if(!policy) status = SOPC_STATUS_NOK;
    if(status == SOPC_STATUS_OK)
        status = SOPC_SecurityConfig_SetSecurityModes(policy, SOPC_SecurityModeMask_None);
    if(status == SOPC_STATUS_OK)
        status = SOPC_SecurityConfig_AddUserTokenPolicy(policy, &SOPC_UserTokenPolicy_Anonymous);
    SOPC_PKIProvider *pki = NULL;
    if(status == SOPC_STATUS_OK) status = SOPC_PKIPermissive_Create(&pki);
    if(status == SOPC_STATUS_OK) status = SOPC_ServerConfigHelper_SetPKIprovider(pki);
    if(status == SOPC_STATUS_OK)
        status = SOPC_ServerConfigHelper_SetUserAuthenticationManager(SOPC_UserAuthentication_CreateManager_AllowAll());
    if(status == SOPC_STATUS_OK)
        status = SOPC_ServerConfigHelper_SetUserAuthorizationManager(SOPC_UserAuthorization_CreateManager_AllowAll());
    if(status == SOPC_STATUS_OK) status = address_space(nodeset);
    if(status == SOPC_STATUS_OK) status = SOPC_ServerHelper_StartServer(on_stopped);
    return status;
}

static bool write_counter(unsigned index, uint64_t value) {
    OpcUa_WriteRequest *request = SOPC_WriteRequest_Create(1);
    if(!request) return false;
    SOPC_NodeId node = {.IdentifierType = SOPC_IdentifierType_Numeric, .Namespace = 1, .Data.Numeric = index + 1};
    SOPC_DataValue data;
    SOPC_DataValue_Initialize(&data);
    data.Value.BuiltInTypeId = SOPC_UInt64_Id;
    data.Value.ArrayType = SOPC_VariantArrayType_SingleValue;
    data.Value.Value.Uint64 = value;
    data.SourceTimestamp = SOPC_Time_GetCurrentTimeUTC();
    SOPC_ReturnStatus status = SOPC_WriteRequest_SetWriteValue(request, 0, &node, SOPC_AttributeId_Value, NULL, &data);
    void *response = NULL;
    if(status == SOPC_STATUS_OK) status = SOPC_ServerHelper_LocalServiceSync(request, &response);
    if(status != SOPC_STATUS_OK && status != SOPC_STATUS_TIMEOUT)
        SOPC_EncodeableObject_Delete(&OpcUa_WriteRequest_EncodeableType, (void **)&request);
    bool success = false;
    if(response) {
        SOPC_EncodeableType *type = *(SOPC_EncodeableType **)response;
        if(status == SOPC_STATUS_OK && type == &OpcUa_WriteResponse_EncodeableType) {
            OpcUa_WriteResponse *reply = response;
            success = SOPC_IsGoodStatus(reply->ResponseHeader.ServiceResult) && reply->NoOfResults == 1 &&
                SOPC_IsGoodStatus(reply->Results[0]);
        }
        SOPC_EncodeableObject_Delete(type, &response);
    }
    return success;
}

static bool run_counters(void) {
    uint64_t *values = calloc(count, sizeof(*values));
    uint64_t *counts = measure_server ? calloc(count, sizeof(*counts)) : NULL;
    if(!values || (measure_server && !counts)) {
        free(values);
        free(counts);
        return false;
    }
    bool reported = false, success = true;
    uint64_t due = 0, period = (uint64_t)sampling_ms * 1000000;
    while(!stopped) {
        int command = control();
        if(command < 0) break;
        if(command > 0) due = start_ns;
        uint64_t now = now_ns();
        if(start_ns && now >= end_ns && !reported) {
            if(measure_server) {
                fputs("{\"event\":\"result\",\"counts\":[", output);
                for(unsigned i = 0; i < count; ++i)
                    fprintf(output, "%s%" PRIu64, i ? "," : "", counts[i]);
                fputc(']', output);
                write_intervals();
                fputs("}\n", output);
            } else {
                fputs("{\"event\":\"done\"}\n", output);
            }
            reported = true;
        }
        if(start_ns && due <= now && now < end_ns) {
            for(unsigned i = 0; i < count; ++i) {
                uint64_t admission = now_ns();
                if(admission >= end_ns) break;
                if(i % 256 == 0 && control() < 0) goto cleanup;
                if(!write_counter(i, values[i] + 1)) {
                    fprintf(stderr, "S2OPC local counter write failed at item %u\n", i);
                    success = false;
                    goto cleanup;
                }
                values[i]++;
                if(measure_server && measured_start_ns <= admission && admission < measured_end_ns) {
                    counts[i]++;
                    record_progress(admission, 1);
                }
            }
            due = start_ns + ((now_ns() - start_ns) / period + 1) * period;
        } else {
            uint64_t delay = start_ns && !reported ? (now < due ? due - now : 0) : 10000000;
            if(start_ns && !reported && now < end_ns && delay > end_ns - now) delay = end_ns - now;
            struct pollfd input = {STDIN_FILENO, POLLIN, 0};
            poll(&input, 1, (int)((delay < 10000000 ? delay : 10000000) + 999999) / 1000000);
        }
    }
    if(stopped) success = false;
cleanup:
    free(values);
    free(counts);
    return success;
}

int main(int argc, char **argv) {
    if(argc != 6) return 2;
    const char *nodeset = argv[1];
    arguments(argc - 1, argv + 1);
    recording_enabled = measure_server;
    SOPC_ReturnStatus status = start_server(nodeset);
    bool success = status == SOPC_STATUS_OK;
    if(success) {
        SOPC_Build_Info info = SOPC_ClientServer_GetBuildInfo();
        fprintf(output, "{\"event\":\"ready\",\"sdk\":\"s2opc\",\"version\":\"%s\","
            "\"clock\":\"CLOCK_MONOTONIC\",\"counter_source\":\"application_update\"}\n", info.buildVersion);
        success = run_counters();
        if(!stopped) SOPC_ServerHelper_StopServer();
    } else {
        fprintf(stderr, "S2OPC counter server setup failed (status %d)\n", (int)status);
    }
    SOPC_ServerConfigHelper_Clear();
    SOPC_CommonHelper_Clear();
    free(interval_counts);
    fclose(output);
    return success ? 0 : 1;
}
