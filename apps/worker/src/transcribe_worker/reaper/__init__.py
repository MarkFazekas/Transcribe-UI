"""External pod reaper (architecture §5.2, layer 4), run on a schedule by `reaper.yml`.

It terminates overdue `transcribe-ui-*` pods and cancels overdue queued pods, using only the
deadline encoded in the pod name. It needs no GPU stack and no R2 access.
"""
