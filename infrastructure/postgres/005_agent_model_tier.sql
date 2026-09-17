-- Agent model tier specialization (speed, reasoning, broad_scale, balanced)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS model_tier text NOT NULL DEFAULT 'balanced';
