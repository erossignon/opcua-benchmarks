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

#include "common.h"

#include <open62541/client.h>
#include <open62541/client_config_default.h>
#include <open62541/client_highlevel.h>
#include <open62541/client_highlevel_async.h>
#include <open62541/plugin/certificategroup_default.h>

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

typedef struct ClientContext {
    UA_Client *client;
    /* The LCG that picks the next scalar node, stepped on demand. Storing the
     * whole sequence would be calls x batch_size NodeIds — ten million at
     * batch 1000 over five samples — for a walk over only
     * O6_NODE_COUNT distinct nodes. */
    uint32_t random_state;
    uint64_t checksum;
    size_t max_outstanding;
    size_t async_completed;
    UA_StatusCode async_status;
    /* Batched access. read_ids/write_values are scratch buffers sized to
     * batch_size and refilled before every service call; open62541 encodes
     * the request inside the send call, so one buffer is safe even when
     * several requests are in flight. */
    size_t batch_size;
    UA_ReadValueId *read_ids;
    UA_WriteValue *write_values;
    /* Array access. One node, array_size elements, payload allocated once. */
    size_t array_size;
    UA_NodeId array_node_id;
    UA_Int32 *array_payload;
} ClientContext;

typedef struct AsyncReadContext {
    UA_Boolean complete;
    UA_StatusCode status;
    UA_Int32 value;
} AsyncReadContext;

static UA_NodeId
next_node_id(ClientContext *context) {
    const uint32_t index = o6_next_node_index(&context->random_state);
    return UA_NODEID_NUMERIC(1, O6_FIRST_NODE_ID + index);
}

static void
on_async_read(UA_Client *client, void *userdata, UA_UInt32 request_id,
              UA_StatusCode status, UA_DataValue *value) {
    AsyncReadContext *context = (AsyncReadContext*)userdata;
    (void)client;
    (void)request_id;
    context->status = status;
    if(status == UA_STATUSCODE_GOOD && value && value->hasValue &&
       UA_Variant_hasScalarType(&value->value, &UA_TYPES[UA_TYPES_INT32]))
        context->value = *(UA_Int32*)value->value.data;
    else if(status == UA_STATUSCODE_GOOD)
        context->status = UA_STATUSCODE_BADTYPEMISMATCH;
    context->complete = true;
}

static uint64_t
run_sync_reads(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t index;
    for(index = 0; index < iterations; ++index) {
        const UA_NodeId node_id = next_node_id(context);
        UA_Variant value;
        UA_Variant_init(&value);
        if(UA_Client_readValueAttribute(context->client, node_id,
                                        &value) != UA_STATUSCODE_GOOD ||
           !UA_Variant_hasScalarType(&value, &UA_TYPES[UA_TYPES_INT32]))
            O6_BENCHMARK_DIE("sync read failed or returned a non-Int32");
        context->checksum += (uint64_t)*(UA_Int32*)value.data;
        UA_Variant_clear(&value);
    }
    return context->checksum;
}

static uint64_t
run_async_reads(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t index;
    for(index = 0; index < iterations; ++index) {
        const UA_NodeId node_id = next_node_id(context);
        AsyncReadContext pending = {false, UA_STATUSCODE_GOOD, 0};
        if(UA_Client_readValueAttribute_async(
               context->client, node_id, on_async_read, &pending, NULL) !=
           UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("async read failed");
        while(!pending.complete) {
            if(UA_Client_run_iterate(context->client,
                                     O6_BENCHMARK_EVENT_WAIT_MS) !=
               UA_STATUSCODE_GOOD)
                O6_BENCHMARK_DIE("async read failed");
        }
        if(pending.status != UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("async read failed");
        context->checksum += (uint64_t)pending.value;
    }
    return context->checksum;
}

static void
on_pipelined_read(UA_Client *client, void *userdata, UA_UInt32 request_id,
                  UA_StatusCode status, UA_DataValue *value) {
    ClientContext *context = (ClientContext*)userdata;
    (void)client;
    (void)request_id;
    if(status == UA_STATUSCODE_GOOD && value && value->hasValue &&
       UA_Variant_hasScalarType(&value->value, &UA_TYPES[UA_TYPES_INT32]))
        context->checksum += (uint64_t)*(UA_Int32*)value->value.data;
    else if(status == UA_STATUSCODE_GOOD)
        status = UA_STATUSCODE_BADTYPEMISMATCH;
    if(status != UA_STATUSCODE_GOOD &&
       context->async_status == UA_STATUSCODE_GOOD)
        context->async_status = status;
    ++context->async_completed;
}

static uint64_t
run_async_pipeline(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t issued = 0;
    context->async_completed = 0;
    context->async_status = UA_STATUSCODE_GOOD;
    while(context->async_completed < iterations) {
        while(issued < iterations &&
              issued - context->async_completed < context->max_outstanding) {
            const UA_NodeId node_id = next_node_id(context);
            ++issued;
            if(UA_Client_readValueAttribute_async(
                   context->client, node_id, on_pipelined_read, context,
                   NULL) != UA_STATUSCODE_GOOD)
                O6_BENCHMARK_DIE("pipelined async read failed");
        }
        if(context->async_completed < iterations &&
           UA_Client_run_iterate(context->client,
                                 O6_BENCHMARK_EVENT_WAIT_MS) !=
               UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined async read failed");
        if(context->async_status != UA_STATUSCODE_GOOD) {
            fprintf(stderr, "pipelined async read status: %s (0x%08x)\n",
                    UA_StatusCode_name(context->async_status),
                    (unsigned)context->async_status);
            O6_BENCHMARK_DIE("pipelined async read failed");
        }
    }
    return context->checksum;
}

/* The write benchmark stores each target node's own numeric identifier as
 * its Int32 value, so the value sequence is a pure function of the NodeId
 * sequence and matches the o6 and asyncua clients exactly. */
static uint64_t
run_sync_writes(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t index;
    for(index = 0; index < iterations; ++index) {
        const UA_NodeId node_id = next_node_id(context);
        UA_Int32 value = (UA_Int32)node_id.identifier.numeric;
        UA_Variant variant;
        UA_Variant_init(&variant);
        UA_Variant_setScalar(&variant, &value, &UA_TYPES[UA_TYPES_INT32]);
        if(UA_Client_writeValueAttribute(context->client, node_id, &variant) !=
           UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("sync write failed");
        context->checksum += (uint64_t)value;
    }
    return context->checksum;
}

static void
on_pipelined_write(UA_Client *client, void *userdata, UA_UInt32 request_id,
                   UA_WriteResponse *response) {
    ClientContext *context = (ClientContext*)userdata;
    UA_StatusCode status;
    (void)client;
    (void)request_id;
    status = response ? response->responseHeader.serviceResult
                      : UA_STATUSCODE_BADUNEXPECTEDERROR;
    if(status == UA_STATUSCODE_GOOD && response->resultsSize == 1)
        status = response->results[0];
    else if(status == UA_STATUSCODE_GOOD)
        status = UA_STATUSCODE_BADUNEXPECTEDERROR;
    if(status != UA_STATUSCODE_GOOD &&
       context->async_status == UA_STATUSCODE_GOOD)
        context->async_status = status;
    ++context->async_completed;
}

static uint64_t
run_async_write_pipeline(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t issued = 0;
    context->async_completed = 0;
    context->async_status = UA_STATUSCODE_GOOD;
    while(context->async_completed < iterations) {
        while(issued < iterations &&
              issued - context->async_completed < context->max_outstanding) {
            const UA_NodeId node_id = next_node_id(context);
            UA_Int32 value = (UA_Int32)node_id.identifier.numeric;
            UA_Variant variant;
            UA_Variant_init(&variant);
            UA_Variant_setScalar(&variant, &value, &UA_TYPES[UA_TYPES_INT32]);
            ++issued;
            if(UA_Client_writeValueAttribute_async(
                   context->client, node_id, &variant, on_pipelined_write,
                   context, NULL) != UA_STATUSCODE_GOOD)
                O6_BENCHMARK_DIE("pipelined async write failed");
            context->checksum += (uint64_t)value;
        }
        if(context->async_completed < iterations &&
           UA_Client_run_iterate(context->client,
                                 O6_BENCHMARK_EVENT_WAIT_MS) !=
               UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined async write failed");
        if(context->async_status != UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined async write failed");
    }
    return context->checksum;
}

/* ---------------------------------------------------------------------- */
/* Batched access: batch_size scalar nodes in one Read/Write service call.  */
/* ---------------------------------------------------------------------- */

/* Fill the scratch ReadValueId array with the next batch_size NodeIds. */
static void
fill_read_batch(ClientContext *context) {
    size_t index;
    for(index = 0; index < context->batch_size; ++index) {
        UA_ReadValueId_init(&context->read_ids[index]);
        context->read_ids[index].nodeId = next_node_id(context);
        context->read_ids[index].attributeId = UA_ATTRIBUTEID_VALUE;
    }
}

/* The batched write stores each node's own numeric identifier, matching the
 * scalar case and therefore the o6 and asyncua clients. */
static void
fill_write_batch(ClientContext *context) {
    size_t index;
    for(index = 0; index < context->batch_size; ++index) {
        UA_WriteValue *value = &context->write_values[index];
        UA_WriteValue_init(value);
        value->nodeId = next_node_id(context);
        value->attributeId = UA_ATTRIBUTEID_VALUE;
        value->value.hasValue = true;
        /* The Int32 lives in the WriteValue itself, so the scratch entry owns
         * no heap memory and needs no clear() between calls. */
        value->value.value.type = &UA_TYPES[UA_TYPES_INT32];
        value->value.value.storageType = UA_VARIANT_DATA_NODELETE;
        value->value.value.arrayLength = 0;
        value->value.value.data = &context->write_values[index].nodeId
                                       .identifier.numeric;
        context->checksum += (uint64_t)value->nodeId.identifier.numeric;
    }
}

static uint64_t
run_sync_batch_reads(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t index;
    for(index = 0; index < iterations; ++index) {
        UA_ReadRequest request;
        UA_ReadResponse response;
        fill_read_batch(context);
        UA_ReadRequest_init(&request);
        request.nodesToRead = context->read_ids;
        request.nodesToReadSize = context->batch_size;
        response = UA_Client_Service_read(context->client, request);
        if(response.responseHeader.serviceResult != UA_STATUSCODE_GOOD ||
           response.resultsSize != context->batch_size ||
           !response.results[0].hasValue ||
           !UA_Variant_hasScalarType(&response.results[0].value,
                                     &UA_TYPES[UA_TYPES_INT32]))
            O6_BENCHMARK_DIE("batched read failed or returned a non-Int32");
        context->checksum +=
            (uint64_t)*(UA_Int32*)response.results[0].value.data +
            (uint64_t)response.resultsSize;
        UA_ReadResponse_clear(&response);
    }
    return context->checksum;
}

static uint64_t
run_sync_batch_writes(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t index;
    for(index = 0; index < iterations; ++index) {
        UA_WriteRequest request;
        UA_WriteResponse response;
        fill_write_batch(context);
        UA_WriteRequest_init(&request);
        request.nodesToWrite = context->write_values;
        request.nodesToWriteSize = context->batch_size;
        response = UA_Client_Service_write(context->client, request);
        if(response.responseHeader.serviceResult != UA_STATUSCODE_GOOD ||
           response.resultsSize != context->batch_size ||
           response.results[0] != UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("batched write failed");
        UA_WriteResponse_clear(&response);
    }
    return context->checksum;
}

static void
on_batch_read(UA_Client *client, void *userdata, UA_UInt32 request_id,
              UA_ReadResponse *response) {
    ClientContext *context = (ClientContext*)userdata;
    UA_StatusCode status;
    (void)client;
    (void)request_id;
    status = response ? response->responseHeader.serviceResult
                      : UA_STATUSCODE_BADUNEXPECTEDERROR;
    if(status == UA_STATUSCODE_GOOD) {
        if(response->resultsSize == 0 || !response->results[0].hasValue ||
           !UA_Variant_hasScalarType(&response->results[0].value,
                                     &UA_TYPES[UA_TYPES_INT32]))
            status = UA_STATUSCODE_BADTYPEMISMATCH;
        else
            context->checksum +=
                (uint64_t)*(UA_Int32*)response->results[0].value.data +
                (uint64_t)response->resultsSize;
    }
    if(status != UA_STATUSCODE_GOOD &&
       context->async_status == UA_STATUSCODE_GOOD)
        context->async_status = status;
    ++context->async_completed;
}

static void
on_batch_write(UA_Client *client, void *userdata, UA_UInt32 request_id,
               UA_WriteResponse *response) {
    ClientContext *context = (ClientContext*)userdata;
    UA_StatusCode status;
    (void)client;
    (void)request_id;
    status = response ? response->responseHeader.serviceResult
                      : UA_STATUSCODE_BADUNEXPECTEDERROR;
    if(status == UA_STATUSCODE_GOOD &&
       (response->resultsSize == 0 || response->results[0] != UA_STATUSCODE_GOOD))
        status = UA_STATUSCODE_BADUNEXPECTEDERROR;
    if(status != UA_STATUSCODE_GOOD &&
       context->async_status == UA_STATUSCODE_GOOD)
        context->async_status = status;
    ++context->async_completed;
}

static uint64_t
run_async_batch_reads(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t issued = 0;
    context->async_completed = 0;
    context->async_status = UA_STATUSCODE_GOOD;
    while(context->async_completed < iterations) {
        while(issued < iterations &&
              issued - context->async_completed < context->max_outstanding) {
            UA_ReadRequest request;
            fill_read_batch(context);
            UA_ReadRequest_init(&request);
            request.nodesToRead = context->read_ids;
            request.nodesToReadSize = context->batch_size;
            ++issued;
            if(UA_Client_sendAsyncReadRequest(context->client, &request,
                                              on_batch_read, context, NULL) !=
               UA_STATUSCODE_GOOD)
                O6_BENCHMARK_DIE("pipelined batched read failed");
        }
        if(context->async_completed < iterations &&
           UA_Client_run_iterate(context->client,
                                 O6_BENCHMARK_EVENT_WAIT_MS) !=
               UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined batched read failed");
        if(context->async_status != UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined batched read failed");
    }
    return context->checksum;
}

static uint64_t
run_async_batch_writes(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t issued = 0;
    context->async_completed = 0;
    context->async_status = UA_STATUSCODE_GOOD;
    while(context->async_completed < iterations) {
        while(issued < iterations &&
              issued - context->async_completed < context->max_outstanding) {
            UA_WriteRequest request;
            fill_write_batch(context);
            UA_WriteRequest_init(&request);
            request.nodesToWrite = context->write_values;
            request.nodesToWriteSize = context->batch_size;
            ++issued;
            if(UA_Client_sendAsyncWriteRequest(context->client, &request,
                                               on_batch_write, context,
                                               NULL) != UA_STATUSCODE_GOOD)
                O6_BENCHMARK_DIE("pipelined batched write failed");
        }
        if(context->async_completed < iterations &&
           UA_Client_run_iterate(context->client,
                                 O6_BENCHMARK_EVENT_WAIT_MS) !=
               UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined batched write failed");
        if(context->async_status != UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined batched write failed");
    }
    return context->checksum;
}

/* ---------------------------------------------------------------------- */
/* Array access: one node holding array_size Int32 elements per call.       */
/* ---------------------------------------------------------------------- */

/* An O(1) checksum, so validating never competes with the transfer it
 * measures — an 8.3 M element array would otherwise dominate the sample. */
static void
account_array(ClientContext *context, const UA_Variant *value) {
    if(!UA_Variant_hasArrayType(value, &UA_TYPES[UA_TYPES_INT32]) ||
       value->arrayLength != context->array_size)
        O6_BENCHMARK_DIE("array read returned the wrong type or length");
    context->checksum +=
        (uint64_t)((UA_Int32*)value->data)[0] + (uint64_t)value->arrayLength;
}

static uint64_t
run_sync_array_reads(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t index;
    for(index = 0; index < iterations; ++index) {
        UA_Variant value;
        UA_Variant_init(&value);
        if(UA_Client_readValueAttribute(context->client, context->array_node_id,
                                        &value) != UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("array read failed");
        account_array(context, &value);
        UA_Variant_clear(&value);
    }
    return context->checksum;
}

static uint64_t
run_sync_array_writes(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t index;
    for(index = 0; index < iterations; ++index) {
        UA_Variant variant;
        UA_Variant_init(&variant);
        UA_Variant_setArray(&variant, context->array_payload,
                            context->array_size, &UA_TYPES[UA_TYPES_INT32]);
        variant.storageType = UA_VARIANT_DATA_NODELETE;
        if(UA_Client_writeValueAttribute(context->client,
                                         context->array_node_id, &variant) !=
           UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("array write failed");
        context->checksum += (uint64_t)context->array_size;
    }
    return context->checksum;
}

static void
on_array_read(UA_Client *client, void *userdata, UA_UInt32 request_id,
              UA_ReadResponse *response) {
    ClientContext *context = (ClientContext*)userdata;
    UA_StatusCode status;
    (void)client;
    (void)request_id;
    status = response ? response->responseHeader.serviceResult
                      : UA_STATUSCODE_BADUNEXPECTEDERROR;
    if(status == UA_STATUSCODE_GOOD) {
        if(response->resultsSize != 1 || !response->results[0].hasValue)
            status = UA_STATUSCODE_BADTYPEMISMATCH;
        else
            account_array(context, &response->results[0].value);
    }
    if(status != UA_STATUSCODE_GOOD &&
       context->async_status == UA_STATUSCODE_GOOD)
        context->async_status = status;
    ++context->async_completed;
}

static uint64_t
run_async_array_reads(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t issued = 0;
    context->async_completed = 0;
    context->async_status = UA_STATUSCODE_GOOD;
    while(context->async_completed < iterations) {
        while(issued < iterations &&
              issued - context->async_completed < context->max_outstanding) {
            UA_ReadRequest request;
            UA_ReadValueId read_id;
            UA_ReadValueId_init(&read_id);
            read_id.nodeId = context->array_node_id;
            read_id.attributeId = UA_ATTRIBUTEID_VALUE;
            UA_ReadRequest_init(&request);
            request.nodesToRead = &read_id;
            request.nodesToReadSize = 1;
            ++issued;
            if(UA_Client_sendAsyncReadRequest(context->client, &request,
                                              on_array_read, context, NULL) !=
               UA_STATUSCODE_GOOD)
                O6_BENCHMARK_DIE("pipelined array read failed");
        }
        if(context->async_completed < iterations &&
           UA_Client_run_iterate(context->client,
                                 O6_BENCHMARK_EVENT_WAIT_MS) !=
               UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined array read failed");
        if(context->async_status != UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined array read failed");
    }
    return context->checksum;
}

static uint64_t
run_async_array_writes(void *opaque, size_t iterations) {
    ClientContext *context = (ClientContext*)opaque;
    size_t issued = 0;
    context->async_completed = 0;
    context->async_status = UA_STATUSCODE_GOOD;
    while(context->async_completed < iterations) {
        while(issued < iterations &&
              issued - context->async_completed < context->max_outstanding) {
            UA_WriteRequest request;
            UA_WriteValue write_value;
            UA_WriteValue_init(&write_value);
            write_value.nodeId = context->array_node_id;
            write_value.attributeId = UA_ATTRIBUTEID_VALUE;
            write_value.value.hasValue = true;
            UA_Variant_setArray(&write_value.value.value,
                                context->array_payload, context->array_size,
                                &UA_TYPES[UA_TYPES_INT32]);
            write_value.value.value.storageType = UA_VARIANT_DATA_NODELETE;
            UA_WriteRequest_init(&request);
            request.nodesToWrite = &write_value;
            request.nodesToWriteSize = 1;
            ++issued;
            if(UA_Client_sendAsyncWriteRequest(context->client, &request,
                                               on_batch_write, context,
                                               NULL) != UA_STATUSCODE_GOOD)
                O6_BENCHMARK_DIE("pipelined array write failed");
            context->checksum += (uint64_t)context->array_size;
        }
        if(context->async_completed < iterations &&
           UA_Client_run_iterate(context->client,
                                 O6_BENCHMARK_EVENT_WAIT_MS) !=
               UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined array write failed");
        if(context->async_status != UA_STATUSCODE_GOOD)
            O6_BENCHMARK_DIE("pipelined array write failed");
    }
    return context->checksum;
}

/* Pick the operation for this payload shape. Scalar access keeps the
 * single-target call form, so the batch-of-one number stays comparable with
 * every result measured before batching existed. */
static O6_BenchmarkOperation
select_operation(const O6_BenchmarkOptions *options, UA_Boolean is_write,
                 UA_Boolean is_async) {
    if(options->array_size > 1) {
        if(is_async)
            return is_write ? run_async_array_writes : run_async_array_reads;
        return is_write ? run_sync_array_writes : run_sync_array_reads;
    }
    if(options->batch_size > 1) {
        if(is_async)
            return is_write ? run_async_batch_writes : run_async_batch_reads;
        return is_write ? run_sync_batch_writes : run_sync_batch_reads;
    }
    if(is_async)
        return is_write ? run_async_write_pipeline : run_async_pipeline;
    return is_write ? run_sync_writes : run_sync_reads;
}

int
main(int argc, char **argv) {
    O6_BenchmarkOptions options = {
        1000, 100, 7, O6_DEFAULT_ENDPOINT, NULL, "read", 1, 1,
        1, 1, {0}, 0};
    UA_Boolean is_write;
    ClientContext context;
    UA_StatusCode status;
    size_t index;
    const char *policy = getenv("O6_BENCHMARK_SECURITY_POLICY");
    const UA_Boolean secure =
        policy && strcmp(policy, "Basic256Sha256") == 0;
    UA_ByteString certificate = UA_BYTESTRING_NULL;
    UA_ByteString private_key = UA_BYTESTRING_NULL;
    if(!o6_benchmark_parse_options(argc, argv, &options)) {
        o6_benchmark_usage(argv[0]);
        return EXIT_FAILURE;
    }
    is_write = strcmp(options.operation, "write") == 0;
    context.client = UA_Client_new();
    context.random_state = (uint32_t)options.seed;
    context.checksum = 0;
    context.max_outstanding = options.max_outstanding;
    context.batch_size = options.batch_size;
    context.array_size = options.array_size;
    context.read_ids = NULL;
    context.write_values = NULL;
    context.array_payload = NULL;
    context.array_node_id = UA_NODEID_NUMERIC(
        1, (UA_UInt32)(O6_ARRAY_FIRST_NODE_ID +
                       (options.array_size > 1
                            ? o6_benchmark_array_index(&options)
                            : 0)));
    if(options.batch_size > 1) {
        context.read_ids = (UA_ReadValueId*)malloc(
            options.batch_size * sizeof(UA_ReadValueId));
        context.write_values = (UA_WriteValue*)malloc(
            options.batch_size * sizeof(UA_WriteValue));
        if(!context.read_ids || !context.write_values) {
            free(context.read_ids);
            free(context.write_values);
            return EXIT_FAILURE;
        }
    }
    if(options.array_size > 1) {
        /* The payload every implementation writes: element i holds i % 1000. */
        context.array_payload =
            (UA_Int32*)malloc(options.array_size * sizeof(UA_Int32));
        if(!context.array_payload) {
            free(context.read_ids);
            free(context.write_values);
            return EXIT_FAILURE;
        }
        for(index = 0; index < options.array_size; ++index)
            context.array_payload[index] = (UA_Int32)(index % 1000);
    }
    if(!context.client) {
        free(context.read_ids);
        free(context.write_values);
        free(context.array_payload);
        return EXIT_FAILURE;
    }
    if(secure) {
        UA_ClientConfig *config = UA_Client_getConfig(context.client);
        certificate = load_file(getenv("O6_BENCHMARK_CERTIFICATE"));
        private_key = load_file(getenv("O6_BENCHMARK_PRIVATE_KEY"));
        status = UA_ClientConfig_setDefaultEncryption(
            config, certificate, private_key, NULL, 0, NULL, 0);
        if(status == UA_STATUSCODE_GOOD) {
            UA_String_clear(&config->clientDescription.applicationUri);
            config->clientDescription.applicationUri =
                UA_STRING_ALLOC("urn:o6:benchmark:client");
            config->certificateVerification.clear(
                &config->certificateVerification);
            UA_CertificateGroup_AcceptAll(&config->certificateVerification);
            config->securityMode = UA_MESSAGESECURITYMODE_SIGNANDENCRYPT;
            config->securityPolicyUri = UA_STRING_ALLOC(
                "http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256");
        }
    } else {
        status = UA_ClientConfig_setDefault(UA_Client_getConfig(context.client));
    }
    UA_ByteString_clear(&certificate);
    UA_ByteString_clear(&private_key);
    if(status == UA_STATUSCODE_GOOD) {
        UA_ClientConfig *client_config = UA_Client_getConfig(context.client);
        client_config->timeout = O6_BENCHMARK_REQUEST_TIMEOUT_MS;
        /* open62541 admits 32 outstanding application service calls by default
         * and refuses the next one with BadTooManyOperations. Every async send
         * in this client aborts on a non-GOOD status, so a matrix asking for a
         * deeper pipeline than that would not measure a slower server — it
         * would kill the worker. Raised to exactly the depth this run was told
         * to keep in flight, which is the invariant the issue loops maintain.
         * The fixed cap lives in :file:`common/contract.h` so the same fix is
         * applied once, here and in the other two suites. */
        o6_configure_client(client_config, options.max_outstanding);
    }
    if(status == UA_STATUSCODE_GOOD)
        status = UA_Client_connect(context.client, options.endpoint);
    if(status != UA_STATUSCODE_GOOD) {
        fprintf(stderr, "Connect failed: %s\n", UA_StatusCode_name(status));
        UA_Client_delete(context.client);
        free(context.read_ids);
        free(context.write_values);
        free(context.array_payload);
        return EXIT_FAILURE;
    }
    if(options.worker_mode) {
        O6_BenchmarkOperation operation = NULL;
        const char *name = NULL;
        if(strcmp(options.worker_mode, "sync") == 0) {
            operation = select_operation(&options, is_write, false);
            name = is_write ? "client_write_sync_concurrent"
                            : "client_read_sync_concurrent";
        } else if(strcmp(options.worker_mode, "async") == 0) {
            operation = select_operation(&options, is_write, true);
            name = is_write ? "client_write_async_concurrent"
                            : "client_read_async_concurrent";
        }
        if(!operation ||
           !o6_benchmark_measure_worker(name, "open62541/open62541", &options,
                                        operation, &context)) {
            UA_Client_disconnect(context.client);
            UA_Client_delete(context.client);
            free(context.read_ids);
            free(context.write_values);
            free(context.array_payload);
            return EXIT_FAILURE;
        }
    } else if(is_write) {
        o6_benchmark_measure("client_write_sync", "open62541/open62541", &options,
                             select_operation(&options, true, false), &context);
        o6_benchmark_measure("client_write_async_concurrent", "open62541/open62541",
                             &options, select_operation(&options, true, true),
                             &context);
    } else {
        o6_benchmark_measure("client_read_sync", "open62541/open62541", &options,
                             select_operation(&options, false, false), &context);
        o6_benchmark_measure("client_read_async_sequential", "open62541/open62541",
                             &options,
                             options.batch_size > 1 || options.array_size > 1
                                 ? select_operation(&options, false, true)
                                 : run_async_reads,
                             &context);
    }
    UA_Client_disconnect(context.client);
    UA_Client_delete(context.client);
    free(context.read_ids);
    free(context.write_values);
    free(context.array_payload);
    return EXIT_SUCCESS;
}
