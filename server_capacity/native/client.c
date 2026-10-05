// SPDX-FileCopyrightText: 2026 o6 Automation GmbH
// All rights reserved.

/* Scalar Read workload with a post-warmup barrier and an absolute monotonic
 * window. Only valid responses received inside that window contribute to
 * requests/second. Drain outcomes remain visible but never inflate the rate. */
#include "hammer_client.h"
#include <open62541/client.h>
#include <open62541/client_config_default.h>
#include <open62541/client_highlevel_async.h>

typedef struct {
    UA_Client *client;
    uint32_t random;
    size_t active;
    UA_Boolean counting;
    UA_Boolean streaming;
    UA_Boolean broken;
    UA_StatusCode refused;
    uint64_t deadline;
    uint64_t attempted;
    uint64_t succeeded;
    uint64_t late;
    uint64_t errors;
    uint64_t cpu_ns;
    uint64_t full_window_ns;
    uint64_t response_wait_ns;
    uint64_t response_wall_start;
    uint64_t response_cpu_start;
    UA_Boolean measuring_response_wait;
    UA_StatusCode error_codes[16];
    uint64_t error_counts[16];
    size_t error_kinds;
    uint64_t other_errors;
    uint64_t histogram[O6_HISTOGRAM_BUCKETS];
} Context;

typedef struct {
    Context *context;
    uint64_t issued;
} Pending;

static uint64_t
cpu_now_ns(void) {
    struct timespec value;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &value);
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) + (uint64_t)value.tv_nsec;
}

static void
finish_response_wait(Context *context) {
    if(!context->measuring_response_wait)
        return;
    context->measuring_response_wait = false;
    const uint64_t now = o6_now_ns();
    const uint64_t end = now < context->deadline ? now : context->deadline;
    const uint64_t wall = end > context->response_wall_start ? end - context->response_wall_start : 0;
    const uint64_t cpu = cpu_now_ns() - context->response_cpu_start;
    context->full_window_ns += wall;
    context->response_wait_ns += wall > cpu ? wall - cpu : 0;
}

static void issue(Context *context);

static void
completed(UA_Client *client, void *userdata, UA_UInt32 id,
          UA_StatusCode status, UA_DataValue *value) {
    (void)client;
    (void)id;
    Pending *pending = (Pending *)userdata;
    Context *context = pending->context;
    /* The event loop may continue polling after the last response. That idle
     * time is not server backpressure and must not enter the wait evidence. */
    if(context->active == 1 && (!context->streaming || o6_now_ns() >= context->deadline))
        finish_response_wait(context);
    context->active--;
    if(context->counting) {
        uint64_t now = o6_now_ns();
        if(status != UA_STATUSCODE_GOOD || !value ||
           (value->hasStatus && value->status != UA_STATUSCODE_GOOD) ||
           !value->hasValue ||
           !UA_Variant_hasScalarType(&value->value, &UA_TYPES[UA_TYPES_INT32])) {
            context->errors++;
            UA_StatusCode code = status;
            if(code == UA_STATUSCODE_GOOD)
                code = value && value->hasStatus && value->status != UA_STATUSCODE_GOOD
                    ? value->status : UA_STATUSCODE_BADTYPEMISMATCH;
            size_t index = 0;
            while(index < context->error_kinds && context->error_codes[index] != code)
                index++;
            if(index < 16) {
                if(index == context->error_kinds)
                    context->error_codes[context->error_kinds++] = code;
                context->error_counts[index]++;
            } else {
                context->other_errors++;
            }
        } else if(now < context->deadline) {
            context->succeeded++;
            context->histogram[o6_histogram_bucket_for((now - pending->issued) / 1000)]++;
        } else {
            context->late++;
        }
    }
    free(pending);
    if(context->streaming && !context->broken && !context->refused && o6_now_ns() < context->deadline)
        issue(context);
}

static void
issue(Context *context) {
    Pending *pending = (Pending *)malloc(sizeof(Pending));
    if(!pending) {
        context->refused = UA_STATUSCODE_BADOUTOFMEMORY;
        return;
    }
    pending->context = context;
    pending->issued = o6_now_ns();
    UA_NodeId node = UA_NODEID_NUMERIC(1, O6_FIRST_NODE_ID + o6_next_node_index(&context->random));
    UA_StatusCode status = UA_Client_readValueAttribute_async(context->client, node, completed, pending, NULL);
    if(status != UA_STATUSCODE_GOOD) {
        free(pending);
        context->refused = status;
        return;
    }
    context->active++;
    if(context->counting)
        context->attempted++;
}

static void
phase(Context *context, const O6_LimitsOptions *options) {
    const uint64_t cpu_start = cpu_now_ns();
    context->streaming = true;
    while(!context->broken && !context->refused && o6_now_ns() < context->deadline) {
        while(!context->refused && context->active < options->max_outstanding && o6_now_ns() < context->deadline)
            issue(context);
        const UA_Boolean full = context->active == options->max_outstanding;
        if(context->counting && full) {
            context->response_cpu_start = cpu_now_ns();
            context->response_wall_start = o6_now_ns();
            context->measuring_response_wait = true;
        }
        if(UA_Client_run_iterate(context->client, 1) != UA_STATUSCODE_GOOD)
            context->broken = true;
        finish_response_wait(context);
    }
    context->streaming = false;
    /* Snapshot before draining. Response waiting subtracts only CPU within
     * the response pump, including requests replenished by callbacks. */
    if(context->counting)
        context->cpu_ns = cpu_now_ns() - cpu_start;
    const uint64_t drain_end = context->deadline + options->grace_ms * UINT64_C(1000000);
    while(context->active && !context->broken && o6_now_ns() < drain_end) {
        if(UA_Client_run_iterate(context->client, 1) != UA_STATUSCODE_GOOD)
            context->broken = true;
    }
}

int main(int argc, char **argv) {
    O6_LimitsOptions options = {
        .endpoint = O6_DEFAULT_ENDPOINT, .duration_ms = 5000,
        .warmup_ms = 1000, .timeout_ms = 2000, .grace_ms = 2500,
        .max_outstanding = 1, .seed = 1,
    };
    if(!o6_limits_parse_options(argc, argv, &options) || options.open_loop ||
       strcmp(options.service, O6_LIMITS_SERVICE_READ) != 0 ||
       options.grace_ms < options.timeout_ms || options.max_outstanding > UINT32_MAX)
        return EXIT_FAILURE;
    Context context = {0};
    context.random = options.seed;
    context.client = UA_Client_new();
    if(!context.client)
        return EXIT_FAILURE;
    UA_ClientConfig *config = UA_Client_getConfig(context.client);
    UA_StatusCode status = UA_ClientConfig_setDefault(config);
    config->timeout = (UA_UInt32)options.timeout_ms;
    o6_configure_client(config, options.max_outstanding);
    if(status == UA_STATUSCODE_GOOD)
        status = UA_Client_connect(context.client, options.endpoint);
    if(status != UA_STATUSCODE_GOOD) {
        fprintf(stderr, "connect failed: %s\n", UA_StatusCode_name(status));
        UA_Client_delete(context.client);
        return EXIT_FAILURE;
    }

    /* Establish every session before any client starts flooding the server;
     * otherwise early clients can starve later session handshakes. */
    puts("O6_CAPACITY_CONNECTED");
    fflush(stdout);
    char command[16];
    if(scanf("%15s", command) != 1 || strcmp(command, "WARMUP") != 0) {
        UA_Client_delete(context.client);
        return EXIT_FAILURE;
    }
    context.deadline = o6_now_ns() + options.warmup_ms * UINT64_C(1000000);
    phase(&context, &options);
    if(context.active || context.broken || context.refused) {
        fprintf(stderr, "warmup failed: active=%zu broken=%d refused=%s\n",
                context.active, context.broken, UA_StatusCode_name(context.refused));
        UA_Client_delete(context.client);
        return EXIT_FAILURE;
    }
    puts("O6_CAPACITY_READY");
    fflush(stdout);
    uint64_t start;
    if(scanf("%" SCNu64, &start) != 1 || start <= o6_now_ns()) {
        UA_Client_delete(context.client);
        return EXIT_FAILURE;
    }
    while(o6_now_ns() < start) {
        struct timespec pause = {0, 100000};
        nanosleep(&pause, NULL);
    }
    const uint64_t actual_start = o6_now_ns();
    context.deadline = start + options.duration_ms * UINT64_C(1000000);
    context.counting = true;
    phase(&context, &options);
    context.counting = false;
    printf("{\"start_ns\":%" PRIu64 ",\"end_ns\":%" PRIu64
           ",\"actual_start_ns\":%" PRIu64 ",\"attempted\":%" PRIu64
           ",\"succeeded\":%" PRIu64 ",\"late\":%" PRIu64
           ",\"errors\":%" PRIu64 ",\"abandoned\":%zu"
           ",\"cpu_ns\":%" PRIu64 ",\"full_window_ns\":%" PRIu64
           ",\"response_wait_ns\":%" PRIu64
           ",\"broken\":%d,\"send_refused\":%u,\"histogram_us\":[",
           start, context.deadline, actual_start, context.attempted,
           context.succeeded, context.late, context.errors, context.active,
           context.cpu_ns, context.full_window_ns, context.response_wait_ns,
           context.broken, (unsigned)context.refused);
    for(size_t i = 0; i < O6_HISTOGRAM_BUCKETS; i++)
        printf("%s%" PRIu64, i ? "," : "", context.histogram[i]);
    printf("],\"error_statuses\":{");
    for(size_t i = 0; i < context.error_kinds; i++)
        printf("%s\"0x%08x %s\":%" PRIu64, i ? "," : "",
               (unsigned)context.error_codes[i], UA_StatusCode_name(context.error_codes[i]),
               context.error_counts[i]);
    printf("%s\"other\":%" PRIu64 "}}\n", context.error_kinds ? "," : "", context.other_errors);
    fflush(stdout);
    UA_Client_delete(context.client);
    return EXIT_SUCCESS;
}
