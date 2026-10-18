from langchain_core.prompts import PromptTemplate


hypothesis_generation_template = PromptTemplate(
    input_variables=["bug_report"],
    template="""You are a software engineer investigating a bug report.
Your task is to generate a list of possible hypotheses that could explain the bug described in the report
Bug Report:
{bug_report}
Please provide a list of hypotheses, each on a new line, that could explain the bug described in the report. Be concise and specific in your hypotheses.
"""
)