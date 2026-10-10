"""Production pipeline: a script written in the conversation (JSON) → narration → character
references → keyframes → clips → final video.

Every stage writes its output into the project folder and can be re-run on its own; importing a
revised script (`ai-video script`) redoes only what the revision touches.
"""
