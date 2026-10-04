# Design departures

Where the built product differs from our Figma (the Designathon submission) and the approved HTML prototypes, and
why. **The design owners keep this file current** (see `docs/BUILD_PLAN.md`); a fidelity issue that we decide not
to fix is recorded here with its reason. Screens: export the Figma frames to `docs/design/` so each row can link
to its frame.

| # | Screen | Design | Built | Why |
|---|---|---|---|---|
| 1 | All | Separate apps per role | One web app with one sign-in; the account decides the face. No role switcher. | One URL for judges and staff; access is enforced by the server, not by which app is installed |
| 2 | Dispatcher | Dark theme only | Dark and light themes (toggle in the left rail) | Bright offices and projectors; same layout and tokens |
| 3 | Dispatcher | Static demo data | Live data from the planner; numbers, trips and explanations change with every plan | It is the working product, not a mock-up |
| 4 | Dispatcher | Clock in the command bar | The virtual demo clock sits under the page title and opens the Demo menu | The command bar overflowed at 1440 px once real counts were shown |
| 5 | Loader | Tablet layout | The same screens collapse to a phone layout below 860 px (queue, then the truck, with back navigation) | Loaders also use phones; judges may test on one |
| 6 | Loader | — | "Who's loading?" crew picker on the shared tablet; every tick carries the name | Accountability on a shared device |
| 7 | Driver | Separate visual style | Rebuilt on the loader's design system (type, spacing, colours, components) with light and dark themes | One family of field apps; dark mode for night runs |
| 8 | Driver | English | English, Sinhala and Tamil, switchable in **Me** and saved to the account | Drivers' first languages |
| 9 | Driver | — | Offline banner, Sync tab and "Test offline mode" | Honest states for the dark corridor; a safe way to demonstrate it |
| 10 | Store | Mobile frames | Desktop layout with a sidebar from 900 px; bottom tabs on phones | Store managers use both |
| 11 | Store | "Arrival time" shown before planning | "Set at 4 PM" until the plan is published | No promise before there is a plan |
| 12 | Driver | Unload list with a count stepper on every line; a Partial outcome | A read-only **Delivery receipt**: each line shows what was ordered and, when the dock loaded less, "M of N loaded · K short at the dock". Outcomes are Delivered, Refused and Store closed; a short load is recorded as partial by itself | Counting belongs to the loader (count out) and the store manager (count in, on Confirm receipt). A third count at the door was slow and could disagree with both |
| 13 | Driver | Photo and finger signature | Photo and a **store-manager PIN handover**: the driver hands over the phone, the manager types their fixed delivery PIN into a masked field, and the phone checks it with no signal. After three wrong tries the driver can record without the PIN; the store and dispatch are told | A squiggle cannot be checked and proves little. The PIN is tied to the account of the person who received the goods, and the server checks it again when the record syncs |
| 14 | Store | Four tabs; sign out only in the desktop sidebar | A **Profile** page (`/store/me`) with the delivery PIN (masked, with an eye to reveal it) and Sign out, opened from the sidebar's user card on desktop and from a fifth **Me** tab on phones | The manager needs to find their PIN, and phone users had no way to sign out |
| 15 | Store | Route strip with "You" in the store's chip | A caption ("Your store is stop 3 of 5 on VEH057's run") over numbered chips; the store's chip carries a store icon, and the chips no longer look clickable | "You" read like a button and did not say which stop or whose run |
| 16 | _team_ | | | |

## Not built (scope)

- Stock levels and inventory at depots and outlets: Relay plans and tracks orders.
- _team: anything in Figma that was left out, and why._
