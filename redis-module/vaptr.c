#include "redismodule.h"
#include <stdio.h>

int VAPtrCommand(RedisModuleCtx *ctx, RedisModuleString **argv, int argc) {
    if (argc < 2)
        return RedisModule_WrongArity(ctx);

    RedisModule_ReplyWithArray(ctx, argc - 1);

    for (int i = 1; i < argc; i++) {
        RedisModuleKey *key = RedisModule_OpenKey(ctx, argv[i], REDISMODULE_READ);

        RedisModule_ReplyWithArray(ctx, 2);
        RedisModule_ReplyWithString(ctx, argv[i]);

        if (key == NULL || RedisModule_KeyType(key) != REDISMODULE_KEYTYPE_STRING) {
            RedisModule_ReplyWithSimpleString(ctx, "(nil)");
        } else {
            size_t len;
            char *ptr = RedisModule_StringDMA(key, &len, REDISMODULE_READ);
            char buf[32];
            snprintf(buf, sizeof(buf), "0x%lx", (unsigned long)ptr);
            RedisModule_ReplyWithSimpleString(ctx, buf);
        }

        if (key)
            RedisModule_CloseKey(key);
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

    return REDISMODULE_OK;
}
