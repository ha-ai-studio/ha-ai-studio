export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export interface Schema {
  type?: string;
  title?: string;
  description?: string;
  enum?: Json[];
  properties?: Record<string, Schema>;
  required?: string[];
  additionalProperties?: boolean | Schema;
  patternProperties?: Record<string, Schema>;
}
export interface Route {
  broker_id: string | null;
  topic: string;
}
export interface Configuration {
  name: string;
  input: Route;
  output: Route;
  settings: Json;
}
export interface ActionType {
  type: string;
  name: string;
  dynamic?: boolean;
  capabilities?: string[];
  local?: boolean;
  editor?: string | null;
  version?: string;
  commit?: string;
  example?: { request_id?: string; data: Json } | null;
  input_schema: Schema;
  output_schema: Schema;
  settings_schema: Schema;
}
export interface ActionState {
  definition: { id: string; name: string; type: string };
  supported: boolean;
  enabled: boolean;
  busy: boolean;
  config: { draft: Configuration; active: Configuration | null } | null;
  plugin: ActionType | null;
  environment: { status: string; logs: string[] };
  issues: string[];
  mqtt: string;
}
export interface Broker {
  id: string;
  name: string;
  host: string;
  port: number;
  tls: boolean;
  source: string;
  editable: boolean;
  secrets: { username: boolean; password: boolean };
}
export interface Brokers {
  brokers: Broker[];
  default_id: string | null;
}
export interface Discovery {
  config: {
    enabled: boolean;
    broker_id: string | null;
    prefix: string;
    ha_status_topic: string;
  };
  status: string;
  pending_cleanup: number;
  legacy_pending: boolean;
}
export interface Repository {
  kind?: "git" | "local" | "release";
  release?: { tag: string; release_id: number; sha256: string };
  id: string;
  url: string;
  ref: string;
  status: string;
  system: boolean;
  refreshing: boolean;
  can_delete: boolean;
  delete_reason: string | null;
  logs: string[];
  actions: { manifest: { name: string; version: string } }[];
}
export interface BrokerProbe {
  ok: boolean;
  message: string;
  scope: string;
  logs: { elapsed_ms: number; message: string }[];
}
export interface LiveEvent {
  timestamp: string;
  kind: string;
  message: string;
  topic?: string;
  payload?: string;
  truncated?: boolean;
}
export interface LiveData {
  epoch: string;
  cursor: number;
  mqtt: string;
  gap: boolean;
  events: LiveEvent[];
}
export interface ApiResponses {
  devices: { devices: DeviceState[] };
  device: DeviceState;
  "device-read": DeviceState;
  "device-deactivate": DeviceState;
  "delete-device": { devices: DeviceState[] };
  actions: { actions: ActionState[]; types: ActionType[] };
  action: ActionState;
  deactivate: ActionState;
  environment: ActionState;
  "reload-source": ActionState;
  brokers: Brokers;
  "broker-refresh": Brokers;
  "broker-delete": Brokers;
  "broker-test": BrokerProbe;
  discovery: Discovery;
  repositories: { repositories: Repository[]; local_available?: boolean };
  "repository-refresh": { repositories: Repository[] };
  "repository-delete": { repositories: Repository[] };
  "delete-action": unknown;
  test: { mode: string; delivery: string; result: Json };
  live: LiveData;
}

export interface DeviceSensor {
  rate?: {
    source: string;
    factor: number;
    time_base: "second" | "minute" | "hour";
  };
  id: string;
  name: string;
  cumulative: boolean;
  device_class: string;
  unit: string;
  action_id: string;
  reading: string;
  max_change: number | null;
}
export interface DeviceConfig {
  name: string;
  interval_minutes: number;
  publish: boolean;
  sensors: DeviceSensor[];
}
export interface DeviceState {
  logs?: { time: string; message: string }[];
  id: string;
  identity: string;
  draft: DeviceConfig;
  active: DeviceConfig | null;
  enabled: boolean;
  busy: boolean;
  status: string;
  publication: string;
  issue: string | null;
  last_attempt: string | null;
  next_run: number | null;
  values: Record<
    string,
    {
      value?: number | string;
      timestamp?: string;
      status: string;
      error?: string;
    }
  >;
}
