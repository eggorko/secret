import argparse

from secret.app import SecretApp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="secret",
        description="Secret — a terminal app",
    )
    parser.add_argument("--name", "-n", type=str, help="Your name")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = SecretApp(args=args)
    app.run()


if __name__ == "__main__":
    main()
