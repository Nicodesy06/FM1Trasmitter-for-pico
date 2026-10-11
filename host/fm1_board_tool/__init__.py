"""fm1-board-tool: host tools for the FM-1 Transporter on several RP2040 boards.

Board profiles, build, UF2 flashing on the RPI-RP2 boot disk, port discovery,
self-test without an FM-1, and read-only Transporter queries. Nothing in this
package writes to an FM-1; writes stay in recovery/fm1_transporter_recover.py,
behind its own dry run, double-read reference and typed confirmation.
"""

__version__ = "1.0.0"
