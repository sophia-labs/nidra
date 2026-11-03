# SPDX-License-Identifier: Apache-2.0
"""Extra golden captures, one module per porting lane: ``capture(cap, ids)``.

``ids`` is ``capture_dal_golden._fixture_ids()`` (see its keys there); ``cap.run``
takes a unique read name ``"<module>.<function>[__variant]"`` and a thunk.
"""
