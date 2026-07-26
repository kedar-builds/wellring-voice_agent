# Wellring

## Overview

Wellring is a voice-agent-based eldercare health-monitoring system. Elderly users are checked in on via phone calls handled by a voice AI agent (Bolna), which conducts a brief health conversation, captures a transcript, and reports it to a FastAPI backend. The backend scores the interaction for health severity and, when risk is elevated, automatically alerts a caregiver via WhatsApp. The audience is family caregivers who can't personally check in every day and want an automated, low-friction way to catch deteriorating health early.

## Goals

1. Every voice check-in produces a structured, severity-scored assessment with no manual intervention.
2. Caregivers are automatically notified via WhatsApp when a check-in is scored HIGH or CRITICAL.
3. No assessment data is lost or silently dropped between the voice call and the stored record — this has failed once already (an unexplained wipe of `assessments`/`conversations`) and must not recur.

## Core User Flow

1. A phone call connects through Bolna. *(Open question: is the primary flow inbound, outbound-scheduled, or both? Resolve here before building around an assumption.)*
2. Bolna conducts the health check-in and calls the backend's `/assess` tool endpoint with the transcript, detected symptoms, and intent.
3. The backend validates and sanitizes the payload, computes a severity score (informed by symptom detection and interaction history), and logs the interaction.
4. If severity is HIGH or CRITICAL, the backend triggers a WhatsApp alert to the registered caregiver.
5. The assessment and any call recording are persisted for later reference.

## Features

### Voice Intake
- Bolna-driven phone conversation with a symptom-detection tool schema.
- Transcript and structured fields (`intent`, `symptoms`, severity indicators) sent to `/assess`.
- `intent` is a required field — a missing value returns `422`, it does not silently default. (This was previously violated by an undocumented default and has been reverted; treat as settled unless explicitly revisited in `progress-tracker.md`.)

### Assessment Scoring
- Severity enum (e.g. LOW / MEDIUM / HIGH / CRITICAL) with a history-based score multiplier.
- Anonymous/sentinel users are excluded from the score multiplier.

### Caregiver Alerting
- Automatic WhatsApp notification on HIGH/CRITICAL severity via Twilio.
- *(Open question: AiSensy has been mentioned as part of the alerting path — confirm whether it's actually in use or whether Twilio is the sole channel.)*

### Call Recording Storage
- Recordings are uploaded to Backblaze B2 (S3-compatible API), replacing Bolna's transient recording URL with a durable one.

## Scope

### In Scope
- Voice-driven health check-ins and severity scoring.
- Automated caregiver alerting on elevated severity.
- Durable storage of assessments, conversations, and recordings.

### Out of Scope
- No user-facing UI or dashboard — this is backend/API + voice agent only.
- No caregiver-side mobile or web app in current scope.

## Success Criteria

1. A real phone call through Bolna produces a logged, severity-scored assessment in the database.
2. A HIGH or CRITICAL assessment reliably triggers a WhatsApp alert without manual action.
3. No authentication bypass or hardcoded credential exists anywhere in the deployed service.
4. Assessment data survives a Railway redeploy — i.e. is confirmed to live in the managed Postgres database, not an ephemeral container filesystem. *(Currently unconfirmed — see `progress-tracker.md`.)*
