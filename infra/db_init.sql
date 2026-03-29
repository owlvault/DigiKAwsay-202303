-- Schema: digikawsay
-- Run this in Cloud SQL to initialize the MVP tables

CREATE SCHEMA IF NOT EXISTS digikawsay;
SET search_path TO digikawsay;

CREATE TABLE projects (
  project_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name              TEXT NOT NULL,
  seed_prompt       TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  created_by        TEXT,
  status            TEXT DEFAULT 'active' CHECK (status IN ('active','closed','archived'))
);

CREATE TABLE cycles (
  cycle_id          SERIAL,
  project_id        UUID REFERENCES projects(project_id),
  phase             TEXT CHECK (phase IN ('INVESTIGACION','ACCION','PARTICIPACION','CLOSED')),
  started_at        TIMESTAMPTZ DEFAULT NOW(),
  closed_at         TIMESTAMPTZ,
  n_participants_active INTEGER DEFAULT 0,
  n_dialogue_packets INTEGER DEFAULT 0,
  saturation_reached BOOLEAN DEFAULT false,
  saturation_index  FLOAT DEFAULT 0.0,
  consensus_reached BOOLEAN DEFAULT false,
  active_agents     TEXT[],
  PRIMARY KEY (project_id, cycle_id)
);

CREATE TABLE dialogue_states (
  participant_id    TEXT NOT NULL,
  project_id        UUID REFERENCES projects(project_id),
  cycle_id          INTEGER,
  checkpoint_id     TEXT,
  turn_count        INTEGER DEFAULT 0,
  emotional_register TEXT CHECK (emotional_register IN ('OPEN','GUARDED','RESISTANT','DISTRESSED')),
  momentum_score    FLOAT DEFAULT 0.5,
  topics_covered    TEXT[],
  topics_pending    TEXT[],
  saturation_flags  TEXT[],
  active_directive_id TEXT,
  safe_harbor_active BOOLEAN DEFAULT false,
  status            TEXT DEFAULT 'active' CHECK (status IN ('active','timed_out','closed','withdrawn','zombie')),
  last_turn_at      TIMESTAMPTZ,
  PRIMARY KEY (participant_id, project_id, cycle_id)
);

-- Outbox pattern for distributed compensation (V3.1)
CREATE TABLE outbox (
    id SERIAL PRIMARY KEY,
    envelope_id UUID NOT NULL,
    topic TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'DELIVERED', 'FAILED')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    delivered_at TIMESTAMPTZ
);

CREATE TABLE data_gaps (
  gap_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        UUID REFERENCES projects(project_id),
  cycle_id          INTEGER,
  constructo        TEXT NOT NULL,
  agent_id          TEXT NOT NULL,
  cycles_open       INTEGER DEFAULT 1,
  status            TEXT DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED','DEAD_END','OUT_OF_SCOPE')),
  created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE channel_outbound_messages (
  message_id        UUID PRIMARY KEY,
  in_reply_to       UUID,
  participant_id    TEXT NOT NULL,
  project_id        UUID,
  channel           TEXT,
  latency_ms        INTEGER,
  sla_breached      BOOLEAN DEFAULT false,
  created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Directivas pendientes de expertos (patrón Shadowing / Wizard-of-Oz)
-- Desacoplado del checkpoint interno de LangGraph para manipulación segura
CREATE TABLE pending_directives (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  participant_id  TEXT NOT NULL,
  content         TEXT NOT NULL,
  urgency         TEXT DEFAULT 'MEDIUM' CHECK (urgency IN ('LOW', 'MEDIUM', 'HIGH')),
  issued_by       TEXT DEFAULT 'human_investigator',
  status          TEXT DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPLIED')),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  applied_at      TIMESTAMPTZ
);

-- Indices
CREATE INDEX idx_dialogue_states_project ON dialogue_states(project_id, cycle_id);
CREATE INDEX idx_dialogue_states_status ON dialogue_states(status) WHERE status = 'active';
CREATE INDEX idx_data_gaps_open ON data_gaps(project_id) WHERE status = 'OPEN';
CREATE INDEX idx_pending_directives_active ON pending_directives(participant_id) WHERE status = 'PENDING';
