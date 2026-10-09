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

#include <open62541/server.h>
#include <open62541/server_config_default.h>
#include "worker.h"

static UA_UInt64 *values, *measured;

static UA_StatusCode read_counter(UA_Server *server, const UA_NodeId *session_id,
    void *session_context, const UA_NodeId *node_id, void *node_context,
    UA_Boolean source_timestamp, const UA_NumericRange *range, UA_DataValue *result) {
    (void)server;
    (void)session_id;
    (void)session_context;
    (void)node_id;
    if(range) return UA_STATUSCODE_BADINDEXRANGEINVALID;
    UA_UInt64 *counter = (UA_UInt64 *)node_context;
    uint64_t admission = now_ns();
    bool active = start_ns && admission >= start_ns && admission < end_ns;
    UA_UInt64 next = *counter + (active ? 1 : 0);
    UA_StatusCode status = UA_Variant_setScalarCopy(&result->value, &next, &UA_TYPES[UA_TYPES_UINT64]);
    if(status) return status;
    result->hasValue = true;
    if(source_timestamp) {
        result->hasSourceTimestamp = true;
        result->sourceTimestamp = UA_DateTime_now();
    }
    *counter = next;
    if(active && measure_server && admission >= measured_start_ns && admission < measured_end_ns) {
        ++measured[counter - values];
        record_progress(admission, 1);
    }
    return UA_STATUSCODE_GOOD;
}

int main(int argc, char **argv) {
    arguments(argc, argv);
    recording_enabled = measure_server;
    UA_ServerConfig initial_config = {0};
    if(UA_ServerConfig_setMinimal(&initial_config, (UA_UInt16)port, NULL) ||
       configure_clock(initial_config.eventLoop)) {
        UA_ServerConfig_clear(&initial_config);
        return 2;
    }
    UA_Server *server = UA_Server_newWithConfig(&initial_config);
    if(!server) return 2;
    UA_ServerConfig *config = UA_Server_getConfig(server);
    config->samplingIntervalLimits.min = 1;
    config->publishingIntervalLimits.min = 1;
    config->queueSizeLimits.min = 1;
    config->maxMonitoredItems = count;
    config->maxMonitoredItemsPerSubscription = count;
    config->maxNotificationsPerPublish = UINT32_MAX;
    values = (UA_UInt64 *)calloc(count, sizeof(*values));
    measured = measure_server ? (UA_UInt64 *)calloc(count, sizeof(*measured)) : NULL;
    if(!values || (measure_server && !measured)) return 2;
    UA_DataSource source = {read_counter, NULL};
    for(unsigned i = 0; i < count; ++i) {
        UA_VariableAttributes attr = UA_VariableAttributes_default;
        attr.displayName = UA_LOCALIZEDTEXT("en-US", "Counter");
        attr.dataType = UA_TYPES[UA_TYPES_UINT64].typeId;
        attr.valueRank = UA_VALUERANK_SCALAR;
        attr.minimumSamplingInterval = 1;
        UA_Variant_setScalar(&attr.value, &values[i], &UA_TYPES[UA_TYPES_UINT64]);
        if(UA_Server_addDataSourceVariableNode(server, UA_NODEID_NUMERIC(1, i + 1),
            UA_NODEID_NUMERIC(0, UA_NS0ID_OBJECTSFOLDER), UA_NODEID_NUMERIC(0, UA_NS0ID_ORGANIZES),
            UA_QUALIFIEDNAME(1, "Counter"), UA_NODEID_NUMERIC(0, UA_NS0ID_BASEDATAVARIABLETYPE), attr,
            source, &values[i], NULL)) return 2;
    }
    if(UA_Server_run_startup(server)) return 2;
    fprintf(output, "{\"event\":\"ready\",\"sdk\":\"open62541\",\"clock\":\"CLOCK_MONOTONIC\","
            "\"sdk_clock\":\"CLOCK_MONOTONIC\",\"counter_source\":\"read_callback\"}\n");
    bool finished = false;
    while(true) {
        int command = control();
        if(command < 0) break;
        uint64_t now = now_ns();
        if(start_ns && !finished && now >= end_ns) {
            finished = true;
            if(measure_server) {
                fputs("{\"event\":\"result\",\"counts\":[", output);
                for(unsigned i = 0; i < count; ++i) fprintf(output, "%s%" PRIu64, i ? "," : "", measured[i]);
                fputc(']', output);
                write_intervals();
                fputs("}\n", output);
            } else fputs("{\"event\":\"done\"}\n", output);
        }
        UA_Server_run_iterate(server, false);
        idle();
    }
    UA_Server_run_shutdown(server);
    UA_Server_delete(server);
    free(values);
    free(measured);
    free(interval_counts);
    return 0;
}
