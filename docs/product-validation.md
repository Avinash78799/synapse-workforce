# Product validation and first pilot

## Proposed lead workflow

Lead with **founder-managed Zendesk support ticket triage for small ecommerce teams**. Start with one queue and a named human responsible for setup and review. The initial demonstration uses payment complaints only to show investigation, approval and escalation; it does not promise refunds or autonomous resolution. Trust Dashboard evidence and personal onboarding are the service proposition.

Zendesk is the first engineering target. Customer demand, queue volume and willingness to pay remain hypotheses. No clients, interviews, paid pilots, testimonials or measured savings have been recorded. Live OAuth and controlled writes must pass their gates before a real pilot. Shopify, per-action automation settings and reversals do not move ahead of Gate 4.

## Interview plan

Interview 10–15 people who own support, refund, or operations workflows. Use actual recent cases rather than asking whether they like an AI product.

1. Walk through the last duplicate-charge complaint from arrival to resolution.
2. Which systems did you open, and which records were required?
3. How many such cases did your team handle last month?
4. How much active handling time did the last case take? How long was the customer waiting?
5. Who can request, approve, and execute a refund today?
6. When is a second reviewer required? What happens outside business hours?
7. Which errors or repeated claims are most expensive or disruptive?
8. What happens after a payment timeout or an unclear provider response?
9. Which commerce, helpdesk, payment, and identity products must this connect to?
10. What evidence would you need before letting software prepare or execute an action?
11. Who owns a purchase decision, and what comparable work/tools already have budget?
12. Would you test this with sandbox data, then supervised cases? What outcome would justify continuing or paying?

Record each interview's role, company type, systems, case volume, current steps, measured/estimated time (label which), approval rules, required safeguards, buying process, and agreed follow-up. Obtain permission before recording or quoting a customer. Do not collect card numbers or live credentials for discovery.

## Measurement definitions

- Case volume: count of eligible support-triage complaints over the baseline period.
- Active handling time: human minutes spent investigating, deciding, and completing one case. Separate from elapsed waiting time.
- Verified resolution rate: cases whose final provider state and ticket outcome were checked, divided by eligible cases.
- Incorrect action rate: confirmed wrong entity, amount, currency, or unauthorized action divided by executed actions. Report counts as well as rates.
- Human review rate: cases sent to an approver divided by eligible cases.
- Escalation rate: cases left for manual handling divided by eligible cases.
- Cost per verified case: observed model and infrastructure costs plus measured human handling effort, divided by verified cases. Do not treat reservations as invoiced spend.
- Duplicate execution count: more than one provider mutation for the same intended action; investigate every occurrence.

Collect a baseline before the pilot and use comparable cases afterward. No hours-saved, savings, accuracy percentage, or ROI estimate is currently substantiated.

## Pilot acceptance template

Customer: to be chosen. Initial stack: Zendesk, subject to discovery fit and live qualification.
Design partners: target 1–3; none recruited through this build.
Start mode: sandbox, then supervised preparation/approval with explicit customer agreement.
Success thresholds: agree with the partner after baseline collection.
Stop conditions: incorrect/unauthorized financial action, isolation failure, evidence loss, or unresolvable credential exposure.
Daily review owner: named person required.
Incident contact and coverage: agreed before access to live systems.
Exit decision: continue, adjust scope, or stop based on recorded outcomes and partner feedback.

## Connector acceptance checklist

Before any live business effects, verify OAuth/credential scopes and tenant binding, signed webhooks and replay protection, exact entity/amount/currency matching, provider-supported idempotency, safe retries, authoritative status lookup, rate-limit backoff, token rotation/revocation, least privilege, data retention, and rollback/reconciliation behavior. Sandbox success must precede a supervised live pilot.

Commercial pricing, legal terms, company registration, support promises, and privacy/compliance review remain founder/external-review decisions. This document is a discovery and engineering handoff, not a validation result or legal opinion.
