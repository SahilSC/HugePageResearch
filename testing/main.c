#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main() {
    size_t size = 1024 * 1024 * 512; // Allocate 512 MB
    char *ptr = malloc(size);
    
    if (ptr == NULL) {
        perror("malloc");
        return 1;
    }

    // Touch the memory to ensure it is actually backed by pages
    memset(ptr, 1, size);
    
    printf("Allocated 512MB at address: %p\n", (void*)ptr);
    printf("PID: %d\n", getpid());
    printf("Check /proc/%d/maps. Press Enter to exit.\n", getpid());
    
    getchar();
    free(ptr);
    return 0;
}