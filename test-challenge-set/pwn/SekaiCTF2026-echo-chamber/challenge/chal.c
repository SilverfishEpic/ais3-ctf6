#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <pthread.h>
#include <semaphore.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <unistd.h>

#define LISTEN_BACKLOG 128

static int create_server_socket(int port) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) {
    perror("socket");
    return -1;
  }

  int opt = 1;
  if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt)) < 0) {
    perror("setsockopt(SO_REUSEADDR)");
    close(fd);
    return -1;
  }

  struct sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  addr.sin_port = htons((uint16_t)port);

  if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
    perror("bind");
    close(fd);
    return -1;
  }

  if (listen(fd, LISTEN_BACKLOG) < 0) {
    perror("listen");
    close(fd);
    return -1;
  }

  return fd;
}

#define HTTP_VERSION "HTTP/1.1"
#define RESPONSE_MALFORMED                                                     \
  HTTP_VERSION " 500 Error\r\n"                                                \
               "Content-Length: 0\r\n"                                         \
               "\r\n"

#define RESPONSE_GET_BODY "Try Harder.\n"

char *METHODS[] = {"POST", "GET"};

typedef struct Worker {
  struct Worker *next;
  pthread_mutex_t lock;
  sem_t job;
  sem_t started;
  int fd;
} Worker;

typedef struct Header {
  struct Header *next;
  char *val;
  char key[];
} Header;

void guard_region() {
  int rnd;
  if (getrandom(&rnd, sizeof(rnd), 0) != sizeof(rnd)) {
    _exit(1);
  }
  int pages = rnd & 0xffff;
  for (int i = 0; i < pages; i++) {
    mmap(NULL, 0x1000, PROT_NONE, MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
  }
}

unsigned int readn(int fd, char *data, unsigned int len) {
  unsigned int nbytes = 0;
  while (nbytes < len) {
    int chunk = read(fd, &data[nbytes], len - nbytes);
    if (chunk < 0) {
      if (errno == EINTR)
        continue;
      break;
    }
    if (chunk == 0)
      break;
    nbytes += chunk;
  }
  return nbytes;
}

void writen(int fd, const char *data, unsigned int len) {
  unsigned int nbytes = 0;
  while (nbytes < len) {
    int chunk = write(fd, &data[nbytes], len - nbytes);
    if (chunk < 0) {
      if (errno == EINTR)
        continue;
      return;
    }
    if (chunk == 0)
      return;
    nbytes += chunk;
  }
}

void read_until(int fd, char *buf, char *delim, unsigned int size) {
  unsigned int nbytes = 0;
  buf[nbytes] = 0;

  while (nbytes < size - 1) {
    if (readn(fd, &buf[nbytes], 1) != 1)
      break;
    nbytes += 1;
    buf[nbytes] = 0;
    if (strstr(buf, delim))
      break;
  }
}

void respond(int fd, const char *response) {
  (void)writen(fd, response, strlen(response));
}

void respond_int(int fd, unsigned long long value, bool is_signed,
                 unsigned int bytes) {
  char buf[32];
  char *p = &buf[sizeof(buf)];
  unsigned long long mag = value;
  unsigned int bits = bytes * 8;

  if (bits == 0 || bits > 64)
    return;

  if (bits < 64)
    value &= (1ULL << bits) - 1;

  if (is_signed && bits > 0 && (value & (1ULL << (bits - 1)))) {
    writen(fd, "-", 1);
    mag = (~value + 1) & (bits == 64 ? ~0ULL : ((1ULL << bits) - 1));
  } else {
    mag = value;
  }

  do {
    *--p = '0' + (mag % 10);
    mag /= 10;
  } while (mag);

  writen(fd, p, &buf[sizeof(buf)] - p);
}

void response_code(int fd, int code, const char *msg) {
  respond(fd, HTTP_VERSION " ");
  respond_int(fd, code, true, sizeof(code));
  respond(fd, " ");
  respond(fd, msg);
  respond(fd, "\r\n");
}

void response_header(int fd, const char *key, const char *val) {
  respond(fd, key);
  respond(fd, ": ");
  respond(fd, val);
  respond(fd, "\r\n");
}

void response_content_length(int fd, size_t len) {
  respond(fd, "Content-Length: ");
  respond_int(fd, len, false, sizeof(len));
  respond(fd, "\r\n");
}

Header *find_header(Header *headers, const char *key) {
  while (headers) {
    if (strcmp(headers->key, key) == 0)
      return headers;
    headers = headers->next;
  }
  return NULL;
}

void handle_post(int fd, const char *path, const Header *headers, char *body,
                 size_t body_len) {
  response_code(fd, 200, "OK");
  response_header(fd, "PATH", path);
  response_content_length(fd, body_len);
  respond(fd, "\r\n");
  writen(fd, body, body_len);
}

void handle_get(int fd, const char *path, const Header *headers) {
  response_code(fd, 200, "OK");
  response_content_length(fd, strlen(RESPONSE_GET_BODY));
  respond(fd, "\r\n");
  respond(fd, RESPONSE_GET_BODY);
}

void *handle_connection(void *arg) {
  Worker *self = (Worker *)arg;
  char buf[0x1000];
  char path[0x1000];
  char *method;
  Header *headers;
  char *body;
  bool keep_alive;

  guard_region();
  sem_post(&self->started);

  while (1) {
    sem_wait(&self->job);

  keep_going:
    method = NULL;
    headers = NULL;
    body = NULL;
    keep_alive = false;

    read_until(self->fd, buf, "\r\n", sizeof(buf));
    for (int i = 0; i < sizeof(METHODS) / sizeof(METHODS[0]); i++) {
      if (memcmp(buf, METHODS[i], strlen(METHODS[i])) == 0) {
        method = METHODS[i];
        break;
      }
    }

    if (!method)
      goto malformed;

    int len = strlen(buf);
    int method_len = strlen(method);
    if (len < method_len + 1 || buf[method_len] != ' ')
      goto malformed;

    char *buf_path = &buf[method_len + 1];
    int path_len = strchrnul(buf_path, ' ') - buf_path;
    if (buf_path[path_len] != ' ' || path_len >= sizeof(path))
      goto malformed;
    memcpy(path, buf_path, path_len);
    path[path_len] = 0;

    char *ver = &buf_path[path_len + 1];
    if (strcmp(ver, HTTP_VERSION "\r\n") != 0)
      goto malformed;

    while (true) {
      read_until(self->fd, buf, "\r\n", sizeof(buf));
      if (strcmp(buf, "\r\n") == 0)
        break;
      char *end = strstr(buf, "\r\n");
      char *delim = strstr(buf, ": ");
      if (!end || !delim)
        goto malformed;
      char *key = buf;
      char *val = delim + 2;
      *delim = 0;
      *end = 0;

      int key_len = strlen(key);

      Header *header = malloc(sizeof(Header) + key_len + 1 + strlen(val) + 1);
      strcpy(&header->key[0], key);
      strcpy(&header->key[key_len + 1], val);
      header->val = &header->key[key_len + 1];
      header->next = headers;
      headers = header;
    }

    Header *connection = find_header(headers, "Connection");
    keep_alive = connection && strcmp(connection->val, "keep-alive") == 0;

    Header *content_length = find_header(headers, "Content-Length");

    if (strcmp(method, "POST") == 0) {
      if (!content_length)
        goto malformed;

      char *end = NULL;
      size_t body_len = strtoll(content_length->val, &end, 10);
      if (end && *end != 0)
        goto malformed;

      body = malloc(body_len + 1);
      size_t nbytes = readn(self->fd, body, body_len);
      if (nbytes != body_len)
        goto malformed;
      body[nbytes] = 0;
      handle_post(self->fd, path, headers, body, body_len);
    } else {
      handle_get(self->fd, path, headers);
    }
    goto done;

  malformed:
    respond(self->fd, RESPONSE_MALFORMED);

  done:
    free(body);
    while (headers) {
      Header *next = headers->next;
      free(headers);
      headers = next;
    }

    if (keep_alive) {
      goto keep_going;
    } else {
      close(self->fd);
      pthread_mutex_lock(&self->lock);
      self->fd = -1;
      pthread_mutex_unlock(&self->lock);
    }
  }
  return NULL;
}

int main() {
  int port = 9999;
  int listen_fd = -1;
  Worker *workers = NULL;

  setbuf(stdout, NULL);
  setbuf(stdin, NULL);
  signal(SIGPIPE, SIG_IGN);
  guard_region();

  listen_fd = create_server_socket(port);
  if (listen_fd < 0) {
    return 1;
  }

  while (true) {
    struct sockaddr_in addr;
    socklen_t addr_len = sizeof(addr);
    int fd = accept(listen_fd, (struct sockaddr *)&addr, &addr_len);
    if (fd < 0) {
      continue;
    }

    Worker *free = workers;
    while (free) {
      if (pthread_mutex_trylock(&free->lock) == 0) {
        if (free->fd < 0) {
          break;
        }
        pthread_mutex_unlock(&free->lock);
      }
      free = free->next;
    }

    if (free) {
      free->fd = fd;
      pthread_mutex_unlock(&free->lock);
      sem_post(&free->job);
    } else {
      free = malloc(sizeof(Worker));
      free->next = workers;
      workers = free;
      pthread_mutex_init(&free->lock, NULL);
      sem_init(&free->job, 0, 1);
      sem_init(&free->started, 0, 0);
      free->fd = fd;
      pthread_t tid;
      pthread_create(&tid, NULL, handle_connection, free);
      sem_wait(&free->started);
    }
  }
}