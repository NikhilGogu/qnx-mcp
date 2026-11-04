/* Copyright 2026 The qnx-mcp-eval authors
 * SPDX-License-Identifier: Apache-2.0 */
#include <arpa/inet.h>
#include <ctype.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static int open_socket(int port, int do_bind)
{
    struct sockaddr_in addr = { .sin_family = AF_INET, .sin_port = htons(port) };
    int s = socket(AF_INET, SOCK_DGRAM, 0);

    if (s == -1) {
        perror("udpecho: socket");
        return -1;
    }
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (do_bind && bind(s, (struct sockaddr *)&addr, sizeof addr) == -1) {
        perror("udpecho: bind");
        close(s);
        return -1;
    }
    return s;
}

static int serve(int port)
{
    char buf[512];
    int served = 0;
    int s = open_socket(port, 1);

    if (s == -1)
        return 1;
    for (;;) {
        struct sockaddr_in peer;
        socklen_t plen = sizeof peer;
        ssize_t n = recvfrom(s, buf, sizeof buf - 1, 0, (struct sockaddr *)&peer, &plen);

        if (n < 0) {
            perror("udpecho: recvfrom");
            continue;
        }
        buf[n] = '\0';
        served++;
        if (strcmp(buf, "QUIT") == 0) {
            sendto(s, "BYE", 3, 0, (struct sockaddr *)&peer, plen);
            break;
        }
        for (ssize_t i = 0; i < n; i++)
            buf[i] = (char)toupper((unsigned char)buf[i]);
        sendto(s, buf, (size_t)n, 0, (struct sockaddr *)&peer, plen);
    }
    printf("udpecho: served %d messages\n", served);
    close(s);
    return 0;
}

static int send_msg(int port, const char *msg)
{
    struct sockaddr_in addr = { .sin_family = AF_INET, .sin_port = htons(port) };
    struct timeval tv = { .tv_sec = 2 };
    char buf[512];
    ssize_t n;
    int s = open_socket(port, 0);

    if (s == -1)
        return 1;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    if (sendto(s, msg, strlen(msg), 0, (struct sockaddr *)&addr, sizeof addr) == -1) {
        perror("udpecho: sendto");
        close(s);
        return 1;
    }
    n = recv(s, buf, sizeof buf - 1, 0);
    close(s);
    if (n < 0) {
        fprintf(stderr, "udpecho: no reply\n");
        return 1;
    }
    buf[n] = '\0';
    printf("%s\n", buf);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 3 && strcmp(argv[1], "serve") == 0)
        return serve(atoi(argv[2]));
    if (argc == 4 && strcmp(argv[1], "send") == 0)
        return send_msg(atoi(argv[2]), argv[3]);
    fprintf(stderr, "usage: udpecho serve <port> | udpecho send <port> <message>\n");
    return 2;
}
