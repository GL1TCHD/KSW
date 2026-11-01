"""Delivered By now defaults to 'ours' and also applies to credit dispatches
(a credit client collecting with his own tanker). Every existing non-cash
dispatch was stamped 'customer' by the old default without meaning it; left
alone it would now read as a pickup."""


def migrate(cr, version):
    cr.execute("""UPDATE ksw_water_dispatch SET x_collected_by = 'ours'
                  WHERE NOT COALESCE(x_is_cash, FALSE)
                    AND x_collected_by IS DISTINCT FROM 'ours'""")
