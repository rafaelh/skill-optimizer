# When to Mock

Mock at **system boundaries** only:

- External APIs (payment, email, etc.)
- Time/randomness
- File system (sometimes)
- Databases — only when a real DB makes tests slow or hard to isolate (e.g. unit tests for business logic). For repository or integration tests, use a real test DB instead.

Don't mock:

- Your own classes/modules
- Internal collaborators
- Anything you control

Mocking internal collaborators couples tests to implementation structure rather than behavior — when you refactor internals, tests break even though nothing changed from the caller's perspective.

## Designing for Mockability

At system boundaries, design interfaces that are easy to mock:

**1. Use dependency injection**

Pass external dependencies in rather than creating them internally:

```python
# Easy to mock
def process_payment(order, payment_client):
    return payment_client.charge(order.total)

# Hard to mock
def process_payment(order):
    client = StripeClient(os.environ["STRIPE_KEY"])
    return client.charge(order.total)
```

**2. Prefer SDK-style interfaces over generic fetchers**

Create specific functions for each external operation instead of one generic function with conditional logic:

```python
# Good: each function is independently mockable
class ApiClient:
    def get_user(self, user_id): ...
    def get_orders(self, user_id): ...
    def create_order(self, data): ...

# Bad: mocking requires conditional logic inside the mock
class ApiClient:
    def fetch(self, endpoint, method="GET", body=None): ...
```

The SDK approach means:
- Each mock returns one specific shape
- No conditional logic in test setup
- Easier to see which endpoints a test exercises
- Type safety per endpoint
