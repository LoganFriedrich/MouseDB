# The Protocol Manager

This page is for the person setting up a testing schedule. No codebase
knowledge assumed.

A **protocol** is a reusable schedule: an ordered list of **phases** (for
example a training phase, a testing phase, a surgery), each with a number of
days, a tray type, sessions per day, whether weekends count, and which data
(pellet scores, weights, surgery records) are expected. A cohort assigned to a
protocol gets its expected schedule from these phases.

**Where:** MouseDB window -> **Cohort Setup** tab -> the **Manage...** button
next to the protocol list.

## Making a new protocol

1. Press **New**.
2. Type a **Name** (and a description if you like).
3. Press **Add Phase** for each phase, in order, and fill in the dialog.
4. Press **Save Protocol**. The message says "created with N phases".

## Changing an existing protocol

Pick the protocol in the list on the left.

**Change a phase with Edit Phase.** Select the phase's row and press **Edit
Phase**; the dialog shows every value of the phase. The phases table itself
cannot be typed into -- double-clicking a cell does nothing. (It could be typed
into before, and a value typed there was thrown away on Save.)

What happens on **Save Protocol** depends on what you changed:

| you changed | what Save does |
|---|---|
| only the name and/or description | saves them into the protocol. The message says "updated". |
| any phase (added, removed, or any value edited -- including surgery records and staggering, which the table does not show) | does **not** change the protocol. It asks: **Save as a new protocol?** |

**Why phases are never changed in place:** cohorts already assigned to the
protocol were scheduled from its phases. Rewriting them would change what
those cohorts are recorded as having followed.

When asked **Save as a new protocol?**:

- **Yes** -- a new protocol is created under the name in the Name box, with
  every phase as it is in the table. Phase details the table does not show
  (phase type, notes, ladder, food deprivation and the like) are carried over
  from the phases you started from. The original protocol is left exactly as
  it was, name and description included. The message says "New protocol ...
  created ... is unchanged".
- **No** -- nothing is saved. The message says your phase changes are not
  saved; they stay in the table until you pick another protocol.

**The new protocol needs its own name.** If the Name box still holds the name
of an existing protocol (for example the one you started from), nothing is
saved and a message asks you to type a new name and press Save Protocol again.
Two protocols with the same name could only be told apart by an internal
number.

A protocol saved this way from a **variant** (a protocol that inherits its
phases from another) is a complete protocol of its own: it holds all its
phases and no longer follows the protocol the variant came from.

(Before this was fixed, Save on an existing protocol kept only the name and
description, threw every phase change away, and still said "updated".)

## Deleting

**Delete** hides a protocol from the lists (it is deactivated, not removed):
cohorts that already use it keep it.
