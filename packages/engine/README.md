# relay_engine

Waypoint's delivery planner: which order goes on which vehicle and trip tomorrow, in what order, at what times,
and why the orders that wait have to wait. Pure Python on OR-Tools CP-SAT; no database or web imports, so the API,
the tests and the Datathon Task 2B command all call the same code.

```python
from relay_engine import Problem, plan, validate_allocation

solution = plan(Problem(orders, vehicles, districts, allowance, policy="fairness"))
assert not validate_allocation(orders, {v.id: v for v in vehicles}, districts, allowance, solution.assignments)
```

| Module | What it does |
|---|---|
| `core.py` | Data types (`Order`, `Vehicle`, `District`, `Problem`, `Solution`, `Deferral`, `Pin`) and the rule constants |
| `standards.py` | The booklet's planning standard: trip minutes, km and litres |
| `model.py` | Stage 1: the CP-SAT allocation model per depot, with symmetry breaking, pins and a greedy fallback |
| `policies.py` | The three policies (Max throughput, Balanced, Fairness first) as objective weights |
| `planner.py` | `plan()`: parallel depot solves, stage 2, explanations and the repair pass |
| `timetable.py` | Stage 2: stop order, departures (Fresh from 03:30), arrival times and late flags |
| `explain.py` | Why each deferred order waits: **unavoidable** (no feasible vehicle) or **choice** (with its cost) |
| `validate.py` | `validate_allocation()` (a port of the organisers' `check_allocation.py`) and `check_move()` for manual moves |
| `task2b.py` | `python -m relay_engine.task2b`: plans the S1 peak day from `data/private/` and validates it |

Read [docs/PLANNING_ENGINE.md](../../docs/PLANNING_ENGINE.md) for the model, the policies and the explanations.
Tests: `packages/engine/tests/test_engine.py` (`make test-engine`); the demo day under every policy: `make check-plan`.
