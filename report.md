# Question

Investigate `GET /api/events/:id` for an authorization or data-exposure bug. The public events list only returns published, future events, but the detail endpoint appears to fetch any event by ID without checking its status. It also includes the organizer’s email in the response. Can an unauthenticated caller retrieve a draft or cancelled event, or see organizer email? Trace the route through the controller and service, cite the relevant code, and describe a minimal reproduction and expected behavior.

# Final report

### 1. Root cause
**Confirmed Root Cause (H2):** In `services/events.service.js`, the `getEventById` function queries the database via `prisma.event.findUnique` solely by `where: { id: eventId }` without verifying that the event has a status of `'published'` or checking event date constraints. Combined with the public route exposure in `routes/events.routes.js` (H1) and lack of response sanitization in `controllers/events.controller.js` (H4), any unauthenticated caller can retrieve draft or cancelled events. Furthermore, the query explicitly includes the organizer's `email` via the relation select mask (supporting H5).

---

### 2. Evidence
- **E1 (`routes/events.routes.js`, line 19):** Defines `router.get('/:id', getEventById);` without attaching `authMiddleware` or `roleMiddleware`, allowing unauthenticated requests to reach the controller.
- **E2 (`controllers/events.controller.js`, lines 33–36):** `getEventById` passes `req.params.id` directly to `getEventByIdService` (aliased import of `getEventById`) and returns the entity inside `{ success: true, data: result }` without verifying user authentication, checking status, or sanitizing fields.
- **E6 (`services/events.service.js`, line 131):** DB call scanning confirms `getEventById()` performs `event.findUnique` with `where(id)` filter alone.
- **E7 (`services/events.service.js`, lines 127–139):**
  - Lines 131–132: Queries `prisma.event.findUnique({ where: { id: eventId }, ... })` with no conditions on `status` or date.
  - Lines 135–137: Organizer relation explicitly includes `select: { id: true, name: true, email: true }`, exposing the organizer's email address in the response.

---

### 3. Ruled out
- **H3 (Rejected):** The hypothesis asserted that `services/events.service.js` included the organizer relation without a select mask. Evidence **E7** (lines 135–137) disproved this, showing an explicit select mask is in place (`select: { id: true, name: true, email: true }`). The exposure of the email address is due to explicit selection in the mask rather than an omitted mask.

---

### 4. Suggested fix
*(Note: This fix is NOT verified, because this agent operates in a read-only environment.)*

#### Before (`services/events.service.js`):
```javascript
  const event = await prisma.event.findUnique({
    where: { id: eventId },
    include: {
      seatCategories: true,
      organizer: {
        select: { id: true, name: true, email: true }
      }
    }
  });

  if (!event) throw { message: 'Event not found', status: 404 };
```

#### After (`services/events.service.js`):
```javascript
  const event = await prisma.event.findUnique({
    where: { id: eventId },
    include: {
      seatCategories: true,
      organizer: {
        select: { id: true, name: true } // Omit sensitive contact info like email
      }
    }
  });

  // Ensure public requests only view published events
  if (!event || event.status !== 'published') {
    throw { message: 'Event not found', status: 404 };
  }
```
*(If organizers need to view their own draft or cancelled events, authentication context should be passed into the service to allow access if `req.user?.id === event.organizerId`.)*

---

### 5. Limitations
- Redis cache invalidation logic across event status transitions was not thoroughly evaluated.
- Client consumption of `organizer.email` in authorized contexts was not verified.
- The investigation relied on static code inspection and did not run live automated tests or database queries against a running instance.

---

### 6. Reproduction and expected behaviour
*(Note: This request was NOT executed; values are representative placeholders.)*

#### Minimal Reproduction Request:
```bash
curl -X GET "https://<HOST>/api/events/<DRAFT_OR_CANCELLED_EVENT_ID>"
```

#### Current Behavior:
Returns HTTP `200 OK` with full draft/cancelled event details and the organizer's email:
```json
{
  "success": true,
  "data": {
    "id": "<DRAFT_OR_CANCELLED_EVENT_ID>",
    "title": "Draft Event",
    "status": "draft",
    "organizer": {
      "id": "<ORGANIZER_ID>",
      "name": "Jane Doe",
      "email": "organizer@example.com"
    },
    "seatCategories": [],
    "images": []
  }
}
```

#### Expected Behavior:
For unauthenticated callers, accessing an unpublished or non-existent event should return HTTP `404 Not Found`:
```json
{
  "error": "Event not found"
}
```
For published events, public responses should omit private contact details such as the organizer's `email`.

# Run summary

```
steps: 8 | stop reason: concluded | pushbacks: 0
hypotheses:
  H1 [supported] The route handler in routes/events.routes.js exposes GET /:id publicly without attaching auth middleware (middlewares/auth.js), allowing unauthenticated requests to reach the controller.
  H2 [confirmed] The event detail query in services/events.service.js queries Prisma via findUnique with only the id filter, omitting status checks such as status: 'published' or eventDate constraints.
  H3 [rejected] The Prisma query in services/events.service.js includes the organizer relation without a select mask, exposing the organizer's User record including email in the raw output.
  H4 [supported] The controller in controllers/events.controller.js returns the database entity directly to the client without sanitizing sensitive fields like the organizer email or verifying ownership for draft/cancelled events.
  H5 [untested] The Prisma query in services/events.service.js explicitly selects the organizer's email in its relation select mask, exposing private organizer contact information to unauthenticated users.
evidence (tools the agent used):
  E1 read_file {"relative_path": "routes/events.routes.js"}
  E2 view_code_item {"path_hint": "events.controller.js", "follow": 1, "name": "getEventById"}
  E3 view_code_item {"name": "getEventByIdService", "path_hint": "events.service.js", "follow": 1}
  E4 read_file {"relative_path": "services/events.service.js", "start_line": 1, "end_line": 50}
  E5 search_code {"pattern": "getEventById", "file_glob": "*.js"}
  E6 scan_db_calls {"model": "Event", "path_hint": "events"}
  E7 read_file {"relative_path": "services/events.service.js", "start_line": 120, "end_line": 160}
```
