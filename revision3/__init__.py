"""Revision 3: Grid-Tied Trading Architecture with ANSI Protection Relays

Wraps Revision 2 engines (both HMM and Vanilla) in industrial safety relay
supervision implementing 12-zone ANSI electromechanical protection logic.

Core module: revision3.integration_supervisor.Revision3ProtectedSupervisor
"""

from revision3.integration_supervisor import Revision3ProtectedSupervisor

__all__ = ['Revision3ProtectedSupervisor']
