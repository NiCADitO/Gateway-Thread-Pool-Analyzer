"""Per-gateway settings. The only module expected to differ between gateways.

Everything else in src/ is identical on every gateway; this is where the two
facts that cannot be derived live.

Deployed like any other library module, so changing a setting is a push
(plus a restart on 8.3) rather than a Designer edit -- which matters because
on 8.1 the timer event script is a binary resource that cannot be edited from
outside the Designer at all.

Jython 2.7: no f-strings, no comprehensions.
"""

# The tag history provider to historize into -- the name from the gateway's
# Config > Tags > History page. NOT the datasource name, though they are
# usually spelled the same.
#
# There is no sane default. The property defaults to "" on the gateway, and a
# blank provider is not known to store anything, so provisioning refuses to
# run rather than creating 65 tags that look historized and quietly are not.
HISTORY_PROVIDER = ""

# Provision the tag tree once, automatically, on the first timer sample after
# this module is loaded.
#
# WHY THIS IS SAFE, given the rule that a timer must never provision on
# failure: this latches after exactly ONE attempt per module load, success or
# failure. It is not a retry loop. Without the latch, a gateway whose
# provisioning permanently fails would rewrite tag configuration every 10
# seconds forever -- an unbounded stream of config writes aimed at the very
# gateway being measured, which is a far worse outcome than no tags.
#
# Turn it on to commission a gateway; it is harmless to leave on (a second
# run sends an identical payload), but off is the quieter default.
PROVISION_ON_START = False
