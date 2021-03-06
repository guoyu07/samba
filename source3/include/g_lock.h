/*
   Unix SMB/CIFS implementation.
   global locks based on dbwrap and messaging
   Copyright (C) 2009 by Volker Lendecke

   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 3 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#ifndef _G_LOCK_H_
#define _G_LOCK_H_

#include "dbwrap/dbwrap.h"

struct g_lock_ctx;
struct messaging_context;

enum g_lock_type {
	G_LOCK_READ = 0,
	G_LOCK_WRITE = 1,
};

struct g_lock_rec {
	enum g_lock_type lock_type;
	struct server_id pid;
};

struct g_lock_ctx *g_lock_ctx_init(TALLOC_CTX *mem_ctx,
				   struct messaging_context *msg);

struct tevent_req *g_lock_lock_send(TALLOC_CTX *mem_ctx,
				    struct tevent_context *ev,
				    struct g_lock_ctx *ctx,
				    TDB_DATA key,
				    enum g_lock_type type);
NTSTATUS g_lock_lock_recv(struct tevent_req *req);
NTSTATUS g_lock_lock(struct g_lock_ctx *ctx, TDB_DATA key,
		     enum g_lock_type lock_type, struct timeval timeout);
NTSTATUS g_lock_unlock(struct g_lock_ctx *ctx, TDB_DATA key);

NTSTATUS g_lock_write_data(struct g_lock_ctx *ctx, TDB_DATA key,
			   const uint8_t *buf, size_t buflen);

NTSTATUS g_lock_do(TDB_DATA key, enum g_lock_type lock_type,
		   struct timeval timeout,
		   void (*fn)(void *private_data), void *private_data);

int g_lock_locks(struct g_lock_ctx *ctx,
		 int (*fn)(TDB_DATA key, void *private_data),
		 void *private_data);
NTSTATUS g_lock_dump(struct g_lock_ctx *ctx, TDB_DATA key,
		     void (*fn)(const struct g_lock_rec *locks,
				size_t num_locks,
				const uint8_t *data,
				size_t datalen,
				void *private_data),
		     void *private_data);

#endif
