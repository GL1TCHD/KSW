{
    'name': 'KSW Vehicle Allocation: Workshop Scope',
    'version': '19.0.1.0.0',
    'summary': "A transferred vehicle is booked into the workshop by its new department, not its old one",
    'description': """
Bridge between Vehicle Allocation and the workshop's vehicle scope
(KSW_workshop_tanker). The workshop decides whose truck it is from the driver
and the tanker groups; a vehicle's department now counts too:

* a supervisor in a vehicle department no longer books in a vehicle allocated
  to another department (his old driver still being on it does not keep it);
* the users who answer for a department in Vehicle Allocation book in all of
  its vehicles.

Hand edits on the employee (Workshop Vehicles added/removed) still win.
Installs by itself when both modules are present.
    """,
    'author': 'KSW',
    'category': 'Human Resources',
    'depends': ['KSW_fleet_allocation', 'KSW_workshop_tanker'],
    'auto_install': True,
    'installable': True,
    'license': 'LGPL-3',
}
