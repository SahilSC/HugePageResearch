/*
 * Minimal Redis 7.4.2 internal ABI definitions used by vaptr.
 *
 * Sources:
 *   - src/server.h
 *   - src/sds.h
 *   - src/module.c
 *   - src/dict.h
 *
 * This header is intentionally small and version-pinned.
 */

#ifndef REDIS_INTERNALS_7_4_H
#define REDIS_INTERNALS_7_4_H

#include <stddef.h>
#include <stdint.h>

typedef char *sds;

struct __attribute__((__packed__)) sdshdr5 {
    unsigned char flags;
    char buf[];
};

struct __attribute__((__packed__)) sdshdr8 {
    uint8_t len;
    uint8_t alloc;
    unsigned char flags;
    char buf[];
};

struct __attribute__((__packed__)) sdshdr16 {
    uint16_t len;
    uint16_t alloc;
    unsigned char flags;
    char buf[];
};

struct __attribute__((__packed__)) sdshdr32 {
    uint32_t len;
    uint32_t alloc;
    unsigned char flags;
    char buf[];
};

struct __attribute__((__packed__)) sdshdr64 {
    uint64_t len;
    uint64_t alloc;
    unsigned char flags;
    char buf[];
};

#define SDS_TYPE_5 0
#define SDS_TYPE_8 1
#define SDS_TYPE_16 2
#define SDS_TYPE_32 3
#define SDS_TYPE_64 4
#define SDS_TYPE_MASK 7
#define SDS_TYPE_BITS 3
#define SDS_TYPE_5_LEN(f) ((f) >> SDS_TYPE_BITS)
#define SDS_HDR(T, s) ((struct sdshdr##T *)((s) - (sizeof(struct sdshdr##T))))

static inline size_t sdslen(const sds s) {
    unsigned char flags = s[-1];

    switch (flags & SDS_TYPE_MASK) {
    case SDS_TYPE_5:
        return SDS_TYPE_5_LEN(flags);
    case SDS_TYPE_8:
        return SDS_HDR(8, s)->len;
    case SDS_TYPE_16:
        return SDS_HDR(16, s)->len;
    case SDS_TYPE_32:
        return SDS_HDR(32, s)->len;
    case SDS_TYPE_64:
        return SDS_HDR(64, s)->len;
    default:
        return 0;
    }
}

#define LRU_BITS 24
#define OBJ_ENCODING_HT 2

typedef struct redisObject {
    unsigned type : 4;
    unsigned encoding : 4;
    unsigned lru : LRU_BITS;
    int refcount;
    void *ptr;
} robj;

typedef struct redisDb redisDb;
typedef struct dict dict;
typedef struct dictEntry dictEntry;

typedef struct RedisModuleKeyInt {
    void *ctx;
    redisDb *db;
    robj *key;
    robj *value;
} RedisModuleKeyInt;

dictEntry *dictFind(dict *d, const void *key);
void *dictGetVal(const dictEntry *de);

#endif
