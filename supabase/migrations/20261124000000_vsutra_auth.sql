-- Private account storage for the V-Sutra FastAPI service.
-- Do not expose this schema through the Supabase Data API.
create schema if not exists vsutra_private;

create table if not exists vsutra_private.users (
  id text primary key,
  email text not null unique,
  password_hash text not null,
  created_at bigint not null
);

create table if not exists vsutra_private.sessions (
  token_hash text primary key,
  user_id text not null references vsutra_private.users(id) on delete cascade,
  expires_at bigint not null
);
create index if not exists sessions_user_idx on vsutra_private.sessions(user_id);

create table if not exists vsutra_private.password_resets (
  token_hash text primary key,
  user_id text not null references vsutra_private.users(id) on delete cascade,
  expires_at bigint not null
);

create table if not exists vsutra_private.conversations (
  user_id text not null references vsutra_private.users(id) on delete cascade,
  conversation_id text not null unique,
  primary key (user_id, conversation_id)
);
