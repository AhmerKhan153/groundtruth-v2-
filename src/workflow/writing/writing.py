from src.workflow.post_writer import create_post


class WritingWorkflow:
    def write(self, title: str, brief: str, is_rewrite: bool = False) -> str:
        """Return the LinkedIn post body as plain text, grounded in `brief`.

        `brief` is the factual summary produced by the extraction stage, not raw
        article text -- see src/workflow/extraction.
        """
        return create_post(title, brief, is_rewrite=is_rewrite)
