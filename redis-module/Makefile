all: vaptr.so

redismodule.h:
	curl -sL https://raw.githubusercontent.com/redis/redis/7.4.2/src/redismodule.h -o redismodule.h

vaptr.so: vaptr.c redismodule.h
	gcc -shared -fPIC -o vaptr.so vaptr.c -I.

clean:
	rm -f vaptr.so redismodule.h

.PHONY: all clean
