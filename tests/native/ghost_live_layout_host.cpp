// The experimental transport record is separate from captured/saved ghost IPC.
#include "ghost_live_protocol.h"
#include <stdio.h>

int main() {
    printf("{\"header\":%zu,\"packet\":%zu,\"core\":%zu,\"held\":%zu,"
           "\"pad\":%zu,\"valid_fields\":%u,\"version\":%u,\"peers\":%u}",
           sizeof(glive_header), sizeof(glive_packet), offsetof(glive_packet, core),
           offsetof(glive_packet, held), offsetof(glive_packet, pad),
           (unsigned)GLIVE_VALID_FIELDS, (unsigned)GLIVE_VERSION, (unsigned)GLIVE_MAX_PEERS);
    return 0;
}
