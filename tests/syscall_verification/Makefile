CC ?= cc
CPPFLAGS ?= -D_GNU_SOURCE
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -Wall -Wextra -Werror

BINS := split_thp_cli mapping_info self_split_verify cross_process_split_verify
COMMON_OBJ := common.o

.PHONY: all clean

all: $(BINS)

split_thp_cli: split_thp_cli.o $(COMMON_OBJ)
	$(CC) $(CFLAGS) -o $@ $^

mapping_info: mapping_info.o $(COMMON_OBJ)
	$(CC) $(CFLAGS) -o $@ $^

self_split_verify: self_split_verify.o $(COMMON_OBJ)
	$(CC) $(CFLAGS) -o $@ $^

cross_process_split_verify: cross_process_split_verify.o $(COMMON_OBJ)
	$(CC) $(CFLAGS) -o $@ $^

clean:
	rm -f $(BINS) *.o
