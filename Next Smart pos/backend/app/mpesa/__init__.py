"""M-Pesa (Safaricom Daraja) integration.

Everything specific to collecting money by M-Pesa lives in this package:

* ``schemas`` - request/response shapes for the API
* ``models``  - the table that parks a sale until the customer approves
* ``service`` - the Daraja client, STK push and callback handling
* ``routes``  - the ``/api/mpesa`` blueprint

Deliberately kept free of imports so the submodules can be pulled in
independently; ``app.models`` imports ``models`` while the model registry is
still being built, which would deadlock if this file pulled them back in.
"""
