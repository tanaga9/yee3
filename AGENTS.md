# Agent Notes

## Priorities

- Keep initial loading fast for folders containing tens of thousands of images.
- Keep next/previous image navigation fast.
- Treat NAS and other high-latency storage as first-class targets.
- Do not require the user to wait for loading to finish before normal navigation.
- Do not degrade the interaction model during incremental loading.

## Guardrails

- Keep a single-image view at the center.
- Preserve independent vertical and horizontal navigation orders.
- Do not drift toward thumbnail-first or file-manager-style UI.

## References

- `README.md`: product concept and user-facing design intent
- `SPEC.md`: core viewing model and performance requirements
