from __future__ import annotations

import click

from umx.cli import main as umx_main


@click.group(name="mem")
def mem_main() -> None:
    """UMX commands exposed under an AIP-style mem namespace."""


for _command_name, _command in sorted(umx_main.commands.items()):
    mem_main.add_command(_command, _command_name)


@click.group()
def main() -> None:
    """Compatibility entrypoint for AIP-style commands."""


main.add_command(mem_main, "mem")


if __name__ == "__main__":
    main()
