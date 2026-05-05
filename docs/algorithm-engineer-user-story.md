# Algorithm Engineer User Story

The user installs the skill because they need labeled training data for model training. They expect the agent to coordinate the annotation workflow, expose quality risks, and turn feedback into better labels and rules.

## Story

1. The algorithm engineer asks the agent to start an annotation project for a dataset.
2. The agent initializes the project, creates tasks from source data, and shows the Kanban dashboard.
3. The engineer configures stage providers in `llm_profiles.yaml`, choosing OpenAI Responses API or a local LLM CLI such as Codex per stage.
4. The agent runs subagent annotation and QC cycles.
5. QC feedback is recorded against attempts and artifacts.
6. Annotators review feedback, then choose either manual annotation edits or batch/code repair rules.
7. Human Review is requested only after QC when the policy requires user judgment.
8. Accepted data is submitted or merged for training.
9. Over time, the same workflow can evolve into active learning or RL data workflow management.

## Multimodal Extension Example

For image detection, an annotator can invoke a VC detection model, save bounding boxes as annotation artifacts, render an image preview with boxes, inspect whether the result is usable, and then submit it to QC. The same pattern can later support video, point clouds, segmentation, relation extraction, and structured JSON labels.
