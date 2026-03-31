# Phloe Proving Ground — core.md

## Purpose

Automated, repeatable stress testing of the entire Phloe platform across a synthetic
population of 100+ tenant configurations, simulating real-world usage patterns to find
failure points before real clients do.

## Dependency

**Proving Ground depends on Ark Phase 3 (re-provisioning script) before it can run
properly.** It needs a staging environment that can be reliably provisioned and torn
down. That staging environment is what the Ark restore script creates. Do not begin
Proving Ground implementation until Ark Phase 3 is stable.

Proving Ground also requires a second Hetzner server (staging). Estimated cost: €5–10/month
for a CX22/CX32. This server is shared with Ark recovery drills.

## Architecture Overview

### Tenant Definition Layer
YAML manifest per synthetic tenant:
- Business type and name
- Booking paradigm (appointment / class / table / food)
- Configuration surface: workflow attachments, disclaimers, PDF attachments, custom fields
- Staff count and availability patterns
- Product/service catalogue size and pricing
- Typical booking volume and peak patterns
- Payment configuration (Stripe test mode)
- Opening hours and timezone

### Provisioning Engine
Extends Phloe's existing Django management commands:
- Reads tenant manifest, provisions a real Phloe tenant instance on staging
- Seeds staff, services, customers, historical bookings
- Deterministic seeding (same manifest = same data = reproducible)
- Teardown: full removal of tenant stack after test run

### Scenario Engine
Playwright (frontend) + k6 (load generation):
- Customer booking flows: happy path + edge cases per paradigm
- Concurrent booking conflicts (two customers, same slot)
- Payment flow: success, failure, webhook retry storms
- Staff rota conflicts and availability edge cases
- Admin panel operations under load
- Search and filter at scale
- Mobile viewport testing
- Accessibility checks (axe-core)

### Orchestrator
1. Provision tenants from manifest
2. Run scenario suites in parallel where possible
3. Collect results: pass/fail, response times, error logs, failure screenshots
4. Generate HTML report: per-tenant, cross-tenant patterns, regression vs last run
5. Tear down or retain for inspection

### Reporting
Nightly HTML report:
- Pass/fail matrix (tenant type × scenario)
- Response time percentiles (p50, p95, p99)
- Error rate trends over time
- Screenshots and DOM snapshots of failures
- Regression flags (passed yesterday, fails tonight)

## Synthetic Tenant Catalogue (Target: 100+)

### Appointment-Led (DemNurse paradigm)
1. GP surgery — 8 practitioners, 15-min slots, 50+ daily bookings
2. Physiotherapist — solo, 30/60-min mixed slots
3. Dog groomer — 3 staff, variable duration by breed/size
4. Hair salon — 6 stylists, service-dependent duration (30min–3hrs)
5. Barber shop — 4 chairs, 20-min slots, walk-in + booked mix
6. Dentist — 3 dentists + hygienist, mixed appointment types
7. Osteopath — solo, 45-min slots, 6-week follow-up rebooking
8. Tattoo studio — 2 artists, consultations + multi-hour sessions
9. Nail bar — 4 technicians, overlapping service durations
10. Driving instructor — 1 instructor, 1hr/2hr lessons, geographic availability
11. Personal trainer — 1:1 and small group, gym and outdoor locations
12. Counsellor/therapist — solo, 50-min sessions, strict confidentiality
13. Mobile dog walker — geographic zones, pack size limits
14. Photographer — half-day/full-day bookings, seasonal peaks
15. Accountant — 30-min consultations, annual busy period
16. Solicitor — mixed duration, conflict checking
17. Vet practice — 4 vets, emergency slots, species-dependent duration
18. Optician — eye tests + contact lens fits, equipment room booking
19. Chiropodist — 30-min slots, home visit option
20. Massage therapist — 30/60/90-min options, room + practitioner

### Class/Timetable-Led (Ganbarukai paradigm)
21. Karate club — 3 age groups, grading events, trial classes
22. Yoga studio — 8 classes/week, 20 capacity, waitlist
23. Dance school — 15 classes across age/level, termly enrolment
24. Pilates studio — reformer (equipment-limited to 6) + mat (20)
25. Swimming school — pool lane limits, age-gated, term bookings
26. Language school — 6-week courses, multiple levels, placement tests
27. Cooking class — 8 capacity (kitchen stations), materials cost included
28. Art class — kids + adults, materials supplied vs BYO variants
29. Pottery studio — wheel-limited (6), glazing follow-up sessions
30. CrossFit box — WOD classes, 16 capacity, drop-in + membership
31. Music school — group lessons + individual tuition (hybrid paradigm)
32. Baby/toddler group — age-banded, term-based, sibling discounts
33. Dog training class — 8 dogs max, puppy vs adult, outdoor weather dependency
34. Choir — weekly rehearsal, concert ticket sales, no capacity limit
35. Book club — monthly, venue rotation, free + paid events mix
36. Craft workshop (haberdashery) — variable capacity by workshop, materials pricing
37. First aid training — corporate and public, certification requirements
38. Coding bootcamp — 12-week course, cohort-based, prerequisites
39. Fitness bootcamp — outdoor, weather cancellation, 30 capacity
40. Antenatal class — 6-week course, couples pricing, NHS referral pathway

### Table/Reservation-Led (Tavola paradigm)
41. Italian restaurant — 40 covers, 2 sittings, dietary flags
42. Pub with food — 60 covers, walk-in heavy, Sunday roast pre-book
43. Café — 25 covers, no-show problem, 90-min table turn
44. Fine dining — 20 covers, 1 sitting, tasting menu only
45. Fish and chip shop — takeaway + 12 dine-in seats
46. Indian restaurant — 50 covers, buffet nights vs à la carte
47. Cocktail bar — 30 seats, minimum spend, event nights
48. Wine bar — 20 covers, tasting events (class paradigm crossover)
49. Brunch spot — weekend only, 2hr slots, groups to 12
50. Hotel restaurant — room guests + external, breakfast/lunch/dinner split
51. Sushi bar — 8 counter seats + 20 regular, different booking rules per area
52. Pizza restaurant — 35 covers, takeaway collection slots
53. Bistro — 28 covers, pre-theatre menu time-limited
54. Tearoom — 18 covers, afternoon tea (pre-book) vs casual (walk-in)
55. Farm shop café — 30 covers, seasonal hours, outdoor seating weather-dependent

### Menu/Ordering-Led (Pizza Shack paradigm)
56. Pizza delivery — 30-min slots, delivery radius, driver capacity
57. Indian takeaway — phone + online, kitchen capacity throttle
58. Chinese takeaway — peak hour queuing, combo meal builder
59. Fish and chip takeaway — pre-order + walk-in, frying batch limits
60. Burger joint — collection + delivery, customisation
61. Sandwich shop — lunch rush (11:30–14:00), corporate bulk orders
62. Bakery — pre-order for collection, daily specials, sells-out items
63. Juice bar — quick turnaround, loyalty/subscription model
64. Meal prep service — weekly subscription boxes, dietary profiles
65. Catering company — event-based bulk orders, lead time requirements

### Hybrid / Edge Cases (stress the configuration layer)
66. Spa — appointments (treatments) + classes (yoga) + vouchers
67. Golf club — tee time booking + lessons (class) + bar (table)
68. Climbing wall — session booking + courses + birthday parties
69. Escape room — fixed-time slots, group pricing, team-building packages
70. Bowling alley — lane booking + shoe hire + food ordering
71. Soft play centre — session slots + party room + café
72. Campsite — pitch booking (nightly) + activity classes + café
73. Marina — berth booking + sailing lessons + chandlery shop
74. Village hall — room hire (hourly/daily) + class slots + event ticketing
75. Co-working space — hot desk (daily) + meeting room (hourly) + events
76. Gym — class timetable + PT appointments + membership management
77. Farm park — entry ticketing + animal experiences + café
78. Museum — timed entry + workshops + gift shop
79. Dog daycare — day boarding + grooming appointments + training classes
80. Equestrian centre — lesson booking + arena hire + livery management

### Scale / Stress Multipliers (81–100)
Duplicate the most complex configurations above with amplified parameters:
- 10× booking volume
- Maximum staff count (20+)
- Maximum service catalogue (100+ items)
- Concurrent user simulation (50+ simultaneous bookers)
- Webhook backpressure (slow Stripe response simulation)
- Database size stress (10,000+ historical bookings)
- Timezone edge cases (BST/GMT switchover, UTC offset tenants)
- Locale edge cases (currency formatting, date format, phone validation)

## Tech Stack

- **Frontend test runner**: Playwright
- **Load generation**: k6 (preferred) or Locust
- **Orchestration**: Python (consistent with Phloe's Django stack)
- **Tenant provisioning**: Django management commands (extend existing setup_* commands)
- **Reporting**: Static HTML (simple, no server required)
- **CI integration**: GitHub Actions, nightly cron
- **Infrastructure**: Dedicated staging server on Hetzner (separate from production)
  — shared with Ark recovery drills

## Decision Log

### 2026-03-31 — Project Registered
**Context**: Phloe is tested manually against a handful of demo tenants. AI-generated
SaaS platforms are failing in production because configuration combination edge cases
are not surfaced by manual testing.
**Decision**: Create Proving Ground — 100+ synthetic tenants across all paradigms,
automated Playwright + k6 scenario suites, nightly HTML report.
**Rationale**: Proactive quality assurance is a competitive requirement. Manual testing
does not scale and does not surface configuration collision bugs.
**Rejected**: Reactive bug-fixing based on client reports — damages trust and is already
too late.
**Dependency noted**: Proving Ground requires Ark Phase 3 (re-provisioning script) and
a dedicated staging server before implementation can begin. Do not start implementation
until Ark Phase 3 is stable.
