-- À exécuter dans l'éditeur SQL de Supabase (Database > SQL Editor)

create table if not exists scolweb_credentials (
    username text primary key,
    encrypted_password text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    last_sync_at timestamptz,
    last_sync_error text
);

create table if not exists planning_events (
    username text not null references scolweb_credentials(username) on delete cascade,
    event_id text not null,
    title text,
    start_at timestamptz,
    end_at timestamptz,
    all_day boolean,
    class_name text,
    synced_at timestamptz not null default now(),
    primary key (username, event_id)
);

create index if not exists planning_events_start_idx on planning_events (start_at);

-- IMPORTANT (sécurité) :
-- Ces deux tables ne doivent JAMAIS être accessibles avec la clé "anon" côté client
-- (app mobile / site web), seulement avec la clé "service_role", utilisée uniquement
-- côté serveur (FastAPI) et dans le job GitHub Actions.
-- Active Row Level Security pour bloquer tout accès par défaut :
alter table scolweb_credentials enable row level security;
alter table planning_events enable row level security;
-- Aucune policy créée = aucun accès via la clé anon. Seule la clé service_role
-- (qui bypass RLS) peut lire/écrire, ce qui est le comportement voulu ici.
