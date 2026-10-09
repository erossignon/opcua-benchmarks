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

/* The single open62541 benchmark server shared by every suite. */

#include "common/contract.h"

#include <errno.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <open62541/server.h>
#include <open62541/server_config_default.h>
#include <open62541/plugin/certificategroup_default.h>

#ifdef O6_BENCHMARK_EXTENSIONS
UA_Server *o6_extensions_server_new(UA_UInt16 port);
#endif

static UA_ByteString
load_file(const char *path) {
    UA_ByteString value = UA_BYTESTRING_NULL;
    FILE *file = path ? fopen(path, "rb") : NULL;
    long size;
    if(!file)
        return value;
    if(fseek(file, 0, SEEK_END) != 0 || (size = ftell(file)) <= 0 ||
       fseek(file, 0, SEEK_SET) != 0 ||
       UA_ByteString_allocBuffer(&value, (size_t)size) != UA_STATUSCODE_GOOD ||
       fread(value.data, 1, value.length, file) != value.length)
        UA_ByteString_clear(&value);
    fclose(file);
    return value;
}

static int
add_benchmark_variable(UA_Server *server, UA_UInt32 index) {
    char name[32];
    UA_Int32 initial = (UA_Int32)index;
    UA_VariableAttributes attributes = UA_VariableAttributes_default;
    snprintf(name, sizeof(name), "BenchmarkValue%u", (unsigned)index);
    UA_Variant_setScalar(&attributes.value, &initial,
                         &UA_TYPES[UA_TYPES_INT32]);
    attributes.displayName = UA_LOCALIZEDTEXT("en-US", name);
    attributes.accessLevel = UA_ACCESSLEVELMASK_READ | UA_ACCESSLEVELMASK_WRITE;
    attributes.userAccessLevel = attributes.accessLevel;
    return UA_Server_addVariableNode(
        server, UA_NODEID_NUMERIC(1, O6_FIRST_NODE_ID + index),
        UA_NS0ID(OBJECTSFOLDER), UA_NS0ID(ORGANIZES),
        UA_QUALIFIEDNAME(1, name),
        UA_NS0ID(BASEDATAVARIABLETYPE), attributes, NULL, NULL) ==
        UA_STATUSCODE_GOOD;
}

static int
add_benchmark_array(UA_Server *server, UA_UInt32 index, size_t size) {
    char name[48];
    UA_VariableAttributes attributes = UA_VariableAttributes_default;
    UA_StatusCode status;
    size_t element;
    UA_UInt32 dimensions[1] = {0};
    UA_Int32 *payload = (UA_Int32*)UA_Array_new(size, &UA_TYPES[UA_TYPES_INT32]);
    if(!payload)
        return 0;
    for(element = 0; element < size; ++element)
        payload[element] = (UA_Int32)(element % 1000);
    snprintf(name, sizeof(name), "BenchmarkArray%zu", size);
    UA_Variant_setArray(&attributes.value, payload, size,
                        &UA_TYPES[UA_TYPES_INT32]);
    attributes.dataType = UA_TYPES[UA_TYPES_INT32].typeId;
    attributes.valueRank = UA_VALUERANK_ONE_DIMENSION;
    attributes.arrayDimensions = dimensions;
    attributes.arrayDimensionsSize = 1;
    attributes.displayName = UA_LOCALIZEDTEXT("en-US", name);
    attributes.accessLevel = UA_ACCESSLEVELMASK_READ | UA_ACCESSLEVELMASK_WRITE;
    attributes.userAccessLevel = attributes.accessLevel;
    status = UA_Server_addVariableNode(
        server,
        UA_NODEID_NUMERIC(1, O6_ARRAY_FIRST_NODE_ID + index),
        UA_NS0ID(OBJECTSFOLDER), UA_NS0ID(ORGANIZES),
        UA_QUALIFIEDNAME(1, name),
        UA_NS0ID(BASEDATAVARIABLETYPE), attributes, NULL, NULL);
    UA_Array_delete(payload, size, &UA_TYPES[UA_TYPES_INT32]);
    return status == UA_STATUSCODE_GOOD;
}

static int
parse_size_list(const char *text, size_t *values, size_t capacity, size_t *count) {
    const char *cursor = text;
    *count = 0;
    if(!text || *text == '\0')
        return 1;
    while(*cursor != '\0') {
        char *end = NULL;
        unsigned long long parsed;
        size_t existing;
        int duplicate = 0;
        errno = 0;
        parsed = strtoull(cursor, &end, 10);
        if(errno != 0 || end == cursor || parsed == 0)
            return 0;
        for(existing = 0; existing < *count; ++existing) {
            if(values[existing] == (size_t)parsed)
                duplicate = 1;
        }
        if(!duplicate) {
            if(*count >= capacity)
                return 0;
            values[(*count)++] = (size_t)parsed;
        }
        cursor = end;
        if(*cursor == ',')
            ++cursor;
        else if(*cursor != '\0')
            return 0;
    }
    return 1;
}

static int
parse_u64(const char *text, uint64_t *result) {
    char *end = NULL;
    unsigned long long parsed;
    errno = 0;
    parsed = strtoull(text, &end, 10);
    if(errno != 0 || !end || *end != '\0')
        return 0;
    *result = (uint64_t)parsed;
    return 1;
}

int
main(int argc, char **argv) {
    UA_StatusCode status;
    UA_UInt32 index;
    size_t array_sizes[O6_MAX_ARRAY_SIZES];
    size_t array_size_count = 0;
    uint64_t port = 4840;
    int argument;
    const char *security_mode = "None";
    UA_Server *server;
    for(argument = 1; argument < argc; ++argument) {
        if(strcmp(argv[argument], "--array-sizes") == 0 && argument + 1 < argc) {
            if(!parse_size_list(argv[++argument], array_sizes,
                                O6_MAX_ARRAY_SIZES, &array_size_count)) {
                fprintf(stderr, "Invalid --array-sizes list\n");
                return EXIT_FAILURE;
            }
        } else if(strcmp(argv[argument], "--port") == 0 && argument + 1 < argc) {
            if(!parse_u64(argv[++argument], &port) || port == 0 || port > 65535) {
                fprintf(stderr, "Invalid --port value\n");
                return EXIT_FAILURE;
            }
        } else if(strcmp(argv[argument], "--security") == 0 && argument + 1 < argc) {
            security_mode = argv[++argument];
            if(strcmp(security_mode, "None") != 0 &&
               strcmp(security_mode, "Basic256Sha256") != 0) {
                fprintf(stderr, "Unknown --security value\n");
                return EXIT_FAILURE;
            }
        } else {
            fprintf(stderr,
                    "Usage: %s [--array-sizes N,N,...] [--port N] "
                    "[--security None|Basic256Sha256]\n",
                    argv[0]);
            return EXIT_FAILURE;
        }
    }
#ifdef O6_BENCHMARK_EXTENSIONS
    if(strcmp(security_mode, "None") != 0)
        return EXIT_FAILURE;
    server = o6_extensions_server_new((UA_UInt16)port);
#else
    server = UA_Server_new();
#endif
    if(!server)
        return EXIT_FAILURE;
#ifndef UA_ENABLE_ENCRYPTION
    if(strcmp(security_mode, "Basic256Sha256") == 0) {
        fprintf(stderr,
                "This build was configured without encryption support; "
                "Basic256Sha256 is not available.\n");
        return EXIT_FAILURE;
    }
#endif
    if(strcmp(security_mode, "Basic256Sha256") == 0) {
#ifdef UA_ENABLE_ENCRYPTION
        const char *cert_path = getenv("O6_BENCHMARK_CERTIFICATE");
        const char *key_path = getenv("O6_BENCHMARK_PRIVATE_KEY");
        UA_ByteString certificate = load_file(cert_path);
        UA_ByteString private_key = load_file(key_path);
        if(certificate.length == 0 || private_key.length == 0) {
            status = UA_STATUSCODE_BADINVALIDARGUMENT;
        } else {
            status = UA_ServerConfig_setDefaultWithSecurityPolicies(
                UA_Server_getConfig(server), (UA_UInt16)port, &certificate,
                &private_key, NULL, 0, NULL, 0, NULL, 0);
            if(status == UA_STATUSCODE_GOOD) {
                UA_ServerConfig *config = UA_Server_getConfig(server);
                UA_String_clear(
                    &config->applicationDescription.applicationUri);
                config->applicationDescription.applicationUri =
                    UA_STRING_ALLOC("urn:o6:benchmark:server");
                config->secureChannelPKI.clear(&config->secureChannelPKI);
                UA_CertificateGroup_AcceptAll(&config->secureChannelPKI);
                config->sessionPKI.clear(&config->sessionPKI);
                UA_CertificateGroup_AcceptAll(&config->sessionPKI);
            }
        }
        UA_ByteString_clear(&certificate);
        UA_ByteString_clear(&private_key);
#endif
    } else {
#ifdef O6_BENCHMARK_EXTENSIONS
        status = UA_STATUSCODE_GOOD;
#else
        /* setMinimal rather than setDefault so --port is honoured without a
         * second bind. */
        status = UA_ServerConfig_setMinimal(UA_Server_getConfig(server),
                                            (UA_UInt16)port, NULL);
#endif
    }
    for(index = 0; status == UA_STATUSCODE_GOOD &&
                   index < O6_NODE_COUNT; ++index) {
        if(!add_benchmark_variable(server, index))
            status = UA_STATUSCODE_BADINTERNALERROR;
    }
    for(index = 0; status == UA_STATUSCODE_GOOD &&
                   index < (UA_UInt32)array_size_count; ++index) {
        if(!add_benchmark_array(server, index, array_sizes[index]))
            status = UA_STATUSCODE_BADINTERNALERROR;
    }
    if(status == UA_STATUSCODE_GOOD) {
        printf("C %s at opc.tcp://127.0.0.1:%" PRIu64 " using #%s\n",
               O6_SERVER_READY, port, security_mode);
        fflush(stdout);
        status = UA_Server_runUntilInterrupt(server);
    }
    UA_Server_delete(server);
    return status == UA_STATUSCODE_GOOD ? EXIT_SUCCESS : EXIT_FAILURE;
}
