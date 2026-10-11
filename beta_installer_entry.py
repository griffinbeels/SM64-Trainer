"""Private offline installer entry; never query or publish a public release."""
from sm64_events.bootstrap.beta_installer import main

if __name__ == "__main__":
    raise SystemExit(main())
