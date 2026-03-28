#include "redismodule.h"
#include "redis_internals_7_4.h"
#include <stdio.h>
#include <string.h>

#define DEFAULT_HASH_FIELD "field0"
#define DEFAULT_PREVIEW_LEN 64

typedef struct VAPtrResult {
    const char *ptr;
    size_t len;
    const char *redis_type;
    const char *encoding;
    size_t encoding_len;
    RedisModuleString *field_name;
    int is_direct;
} VAPtrResult;

static int StringEquals(RedisModuleString *arg, const char *value) {
    size_t arg_len;
    const char *arg_ptr = RedisModule_StringPtrLen(arg, &arg_len);
    size_t value_len = strlen(value);
    return arg_len == value_len && memcmp(arg_ptr, value, value_len) == 0;
}

static RedisModuleCallReply *GetObjectEncodingReply(
    RedisModuleCtx *ctx,
    RedisModuleString *key_name
) {
    return RedisModule_Call(ctx, "OBJECT", "cs", "ENCODING", key_name);
}

static const char *GetObjectEncoding(
    RedisModuleCtx *ctx,
    RedisModuleString *key_name,
    size_t *encoding_len
) {
    RedisModuleCallReply *reply = GetObjectEncodingReply(ctx, key_name);
    if (reply == NULL || RedisModule_CallReplyType(reply) != REDISMODULE_REPLY_STRING) {
        *encoding_len = sizeof("unknown") - 1;
        return "unknown";
    }
    return RedisModule_CallReplyStringPtr(reply, encoding_len);
}

static int LookupHashtableFieldPointer(
    RedisModuleKey *key,
    RedisModuleString *field_name,
    VAPtrResult *result
) {
    RedisModuleKeyInt *internal_key = (RedisModuleKeyInt *)key;
    size_t field_len;
    const char *field_ptr = RedisModule_StringPtrLen(field_name, &field_len);

    if (
        internal_key == NULL ||
        internal_key->value == NULL ||
        internal_key->value->encoding != OBJ_ENCODING_HT ||
        field_ptr == NULL
    ) {
        return 0;
    }

    dict *hash_dict = (dict *)internal_key->value->ptr;
    dictEntry *entry = dictFind(hash_dict, field_ptr);
    if (entry == NULL) {
        return 0;
    }

    sds value = (sds)dictGetVal(entry);
    if (value == NULL) {
        return 0;
    }

    result->ptr = value;
    result->len = sdslen(value);
    result->is_direct = 1;
    return 1;
}

static int LookupValuePointer(
    RedisModuleCtx *ctx,
    RedisModuleKey *key,
    RedisModuleString *key_name,
    RedisModuleString *field_name,
    VAPtrResult *result
) {
    memset(result, 0, sizeof(*result));

    int key_type = RedisModule_KeyType(key);
    if (key_type == REDISMODULE_KEYTYPE_STRING) {
        result->redis_type = "string";
        result->encoding = GetObjectEncoding(ctx, key_name, &result->encoding_len);
        result->ptr = RedisModule_StringDMA(key, &result->len, REDISMODULE_READ);
        result->is_direct = result->ptr != NULL;
        return result->is_direct;
    }

    if (key_type != REDISMODULE_KEYTYPE_HASH) {
        result->redis_type = "unsupported";
        result->encoding = "n/a";
        result->encoding_len = sizeof("n/a") - 1;
        return 0;
    }

    result->redis_type = "hash";
    result->encoding = GetObjectEncoding(ctx, key_name, &result->encoding_len);
    result->field_name = field_name;
    return LookupHashtableFieldPointer(key, field_name, result);
}

static int ParseFieldOption(
    RedisModuleCtx *ctx,
    RedisModuleString **argv,
    int argc,
    RedisModuleString **field_name,
    int *first_key_index
) {
    if (argc >= 4 && StringEquals(argv[1], "FIELD")) {
        *field_name = argv[2];
        *first_key_index = 3;
        return 1;
    }

    *field_name = RedisModule_CreateString(ctx, DEFAULT_HASH_FIELD, sizeof(DEFAULT_HASH_FIELD) - 1);
    *first_key_index = 1;
    return 1;
}

static int ParsePreviewLength(RedisModuleString *arg, long long *preview_len) {
    if (RedisModule_StringToLongLong(arg, preview_len) != REDISMODULE_OK) {
        return 0;
    }
    if (*preview_len < 0) {
        return 0;
    }
    return 1;
}

int VAPtrCommand(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    RedisModule_AutoMemory(ctx);

    if (argc < 2)
        return RedisModule_WrongArity(ctx);

    RedisModuleString *field_name = NULL;
    int first_key_index = 1;
    if (!ParseFieldOption(ctx, argv, argc, &field_name, &first_key_index)) {
        return RedisModule_ReplyWithError(ctx, "ERR invalid VAPTR field options");
    }
    if (first_key_index >= argc) {
        return RedisModule_WrongArity(ctx);
    }

    RedisModule_ReplyWithArray(ctx, argc - first_key_index);

    for (int i = first_key_index; i < argc; i++) {
        RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[i], REDISMODULE_READ);
        VAPtrResult result;

        RedisModule_ReplyWithArray(ctx, 2);
        RedisModule_ReplyWithString(ctx, argv[i]);

        if (key == NULL) {
            RedisModule_ReplyWithCString(ctx, "(nil)");
        } else {
            char buf[32];
            if (!LookupValuePointer(ctx, key, argv[i], field_name, &result)) {
                RedisModule_ReplyWithCString(ctx, "(nil)");
            } else {
                snprintf(buf, sizeof(buf), "0x%lx", (unsigned long)result.ptr);
                RedisModule_ReplyWithCString(ctx, buf);
            }
        }
    }

    return REDISMODULE_OK;
}

int VAPtrDebugCommand(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    RedisModule_AutoMemory(ctx);

    if (argc < 2 || argc > 4)
        return RedisModule_WrongArity(ctx);

    RedisModuleString *field_name = argc >= 3
        ? argv[2]
        : RedisModule_CreateString(ctx, DEFAULT_HASH_FIELD, sizeof(DEFAULT_HASH_FIELD) - 1);
    long long preview_len_ll = DEFAULT_PREVIEW_LEN;
    if (argc == 4 && !ParsePreviewLength(argv[3], &preview_len_ll)) {
        return RedisModule_ReplyWithError(ctx, "ERR preview length must be a non-negative integer");
    }
    size_t preview_len = (size_t)preview_len_ll;

    RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[1], REDISMODULE_READ);
    VAPtrResult result;
    int found_direct = 0;
    if (key != NULL) {
        found_direct = LookupValuePointer(ctx, key, argv[1], field_name, &result);
    } else {
        memset(&result, 0, sizeof(result));
        result.redis_type = "missing";
        result.encoding = "n/a";
        result.encoding_len = sizeof("n/a") - 1;
    }

    RedisModule_ReplyWithArray(ctx, 8);
    RedisModule_ReplyWithString(ctx, argv[1]);
    RedisModule_ReplyWithCString(ctx, result.redis_type != NULL ? result.redis_type : "unknown");
    if (result.encoding != NULL) {
        RedisModule_ReplyWithStringBuffer(ctx, result.encoding, result.encoding_len);
    } else {
        RedisModule_ReplyWithCString(ctx, "unknown");
    }
    RedisModule_ReplyWithCString(ctx, found_direct ? "direct" : "unresolved");
    if (result.field_name != NULL) {
        RedisModule_ReplyWithString(ctx, result.field_name);
    } else {
        RedisModule_ReplyWithCString(ctx, "(nil)");
    }
    if (found_direct) {
        char buf[32];
        snprintf(buf, sizeof(buf), "0x%lx", (unsigned long)result.ptr);
        RedisModule_ReplyWithCString(ctx, buf);
        RedisModule_ReplyWithLongLong(ctx, result.len);
        RedisModule_ReplyWithStringBuffer(
            ctx,
            result.ptr,
            result.len < preview_len ? result.len : preview_len
        );
    } else {
        RedisModule_ReplyWithCString(ctx, "(nil)");
        RedisModule_ReplyWithLongLong(ctx, -1);
        RedisModule_ReplyWithCString(ctx, "(nil)");
    }

    return REDISMODULE_OK;
}

int RedisModule_OnLoad(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    (void)argv;
    (void)argc;

    if (RedisModule_Init(ctx, "vaptr", 1, REDISMODULE_APIVER_1) == REDISMODULE_ERR)
        return REDISMODULE_ERR;

    if (RedisModule_CreateCommand(ctx, "vaptr", VAPtrCommand, "readonly", 1, -1, 1) == REDISMODULE_ERR)
        return REDISMODULE_ERR;
    if (
        RedisModule_CreateCommand(ctx, "vaptr.debug", VAPtrDebugCommand, "readonly", 1, 1, 1)
            == REDISMODULE_ERR
    )
        return REDISMODULE_ERR;

    return REDISMODULE_OK;
}
