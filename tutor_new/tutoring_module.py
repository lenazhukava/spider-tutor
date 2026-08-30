##################################################
#
# PREAMBLE
#
##################################################

#
# Load up the packages
#
import base64
import anthropic
from markitdown import MarkItDown
from pydantic import BaseModel, Field
import os
from pathlib import Path

#
# Setting up the Anthropic Client
#
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=API_KEY)

#
# Setting up default model
#
DEFAULT_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 16384

#
# Setting the file types
#

# File types Claude accepts natively as content blocks
NATIVE_IMAGE_TYPES = {".jpeg", ".jpg", ".png", ".gif", ".webp"}
NATIVE_DOC_TYPES = {".pdf", ".txt"}

# File types to convert via markitdown before sending as text
MARKITDOWN_TYPES = {".xlsx", ".pptx", ".docx", ".md", ".csv", ".html", ".xml"}

IMAGE_MIME_TYPES = {".jpeg": "image/jpeg", ".jpg":  "image/jpeg", ".png":  "image/png", ".gif":  "image/gif", ".webp": "image/webp"}

##################################################
#
# HELPER FUNCTIONS
#
##################################################

#
# Function to deal with files
#
def build_content_blocks(prompt_filepaths: list[str]) -> list[dict]:
    """
    Builds a list of Claude API content blocks from a list of file paths.
    
    Files natively supported by the Claude API (PDFs, images, plain text) are
    attached directly as typed content blocks. Files that require conversion
    (xlsx, pptx, docx, md, csv, html, xml) are converted to Markdown via
    MarkItDown and attached as text blocks. Unknown text-readable files are
    attached as plain text; unsupported binary files are skipped with a warning.

    Args:
        prompt_filepaths (list[str]): A list of file paths to attach. Supported
            types are:
            - Native image blocks: .jpeg, .jpg, .png, .gif, .webp
            - Native document blocks: .pdf, .txt
            - Markitdown-converted text blocks: .xlsx, .pptx, .docx, .md,
              .csv, .html, .xml

    Returns:
        list[dict]: A list of Claude API content block dicts, ready to be
            included in the `content` field of a Messages API request.

    """
    content = []

    for prompt_filepath in prompt_filepaths:
        path = Path(prompt_filepath)
        suffix = path.suffix.lower()  # Normalize to lowercase for consistent matching

        if suffix in NATIVE_IMAGE_TYPES:
            # Claude accepts images natively — send as base64-encoded image block
            raw = path.read_bytes()
            mime = IMAGE_MIME_TYPES[suffix]  # Look up the correct MIME type for this extension
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.standard_b64encode(raw).decode("utf-8"),
                },
            })

        elif suffix == ".pdf":
            # Claude can read PDFs natively, preserving visual layout, figures, and tables
            raw = path.read_bytes()
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(raw).decode("utf-8"),
                },
            })

        elif suffix == ".txt":
            # Plain text can be sent directly without base64 encoding using the "text" source type
            text = path.read_text(encoding="utf-8")
            content.append({
                "type": "document",
                "source": {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": text,
                },
            })

        elif suffix in MARKITDOWN_TYPES:
            # Claude has no native support for these formats — convert to Markdown first
            md_converter = MarkItDown()
            result = md_converter.convert(str(path))
            markdown_text = result.text_content
            content.append({
                "type": "text",
                "text": (
                    f"### Contents of `{path.name}` (converted to Markdown)\n\n"
                    f"{markdown_text}"
                ),
            })

        else:
            # Unknown file type — attempt to read as UTF-8 text and include as-is
            try:
                text = path.read_text(encoding="utf-8")
                content.append({
                    "type": "text",
                    "text": f"### Contents of `{path.name}`\n\n{text}",
                })
            except UnicodeDecodeError:
                # File is binary and has no supported handler — skip it rather than crash
                print(f"Warning: Skipping unsupported binary file: {path.name}")

    return content


# Call LLM Function
# 
def call_llm(
    prompt: str, 
    prompt_filepaths: list,
    system_prompt_filepath: str,
    return_class: type[BaseModel],
    model: str = DEFAULT_MODEL,
    max_tokens: int = MAX_TOKENS,
    **system_prompt_kwargs 
) -> BaseModel:
    """
    Core LLM calling function. Takes a list of prompt content, a filepath to the system 
    prompt, a return class, and any number of keyword arguments to inject into the prompt 
    template and then generates an LLM response.

    Args:
        prompt (str): The prompt to pass to the model
        prompt_filepaths (list): A list of all the files to attach to the model (allowable types are pdf, powerpoint, jpeg, jpg, png, text, md). 
        system_prompt_filepath (str): Path to the system prompt template markdown file.
        return_class (type[BaseModel]): The Pydantic model class to parse the response into.
        model (str): The AI model to use for generation. Defaults to DEFAULT_MODEL.
        **kwargs: Any number of keyword arguments to inject into the prompt template
            as placeholders. For example, if the prompt contains {topic} and {level},
            pass topic="Supply and Demand", level="beginner".

    Return (BaseModel):
        A parsed instance of the return_class provided, populated with the structured response from the LLM.

    """
    
    print(system_prompt_kwargs)
    #
    # Loading up the system prompt.
    #
    file_object = open(system_prompt_filepath, "r")
    if system_prompt_kwargs:
        SYSTEM_PROMPT = file_object.read().format(**system_prompt_kwargs)
    else:
        SYSTEM_PROMPT = file_object.read()
    file_object.close()

    #
    # Construct the prompt
    #
    content = build_content_blocks(prompt_filepaths)
    if prompt != "":
        content.append({"type": "text", "text": prompt})
    #
    # Sending the data + prompt to Anthropic
    #
    response = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=return_class
    )

    return response.parsed_output

##################################################
#
# MODULE FUNCTIONS
#
##################################################

#
# Function to create draft notes
# 

class ChapterNotes(BaseModel):
    thought_process: str = Field(description="The thought process of how to organize the notes and what to include")
    notes: str = Field(description="Notes for the chapter.")
    suggested_subject: str = Field(default="", description="A short 2-4 word subject/course name capturing what this material is from, e.g. 'Intro Microeconomics' or 'Organic Chemistry'. Leave blank if not applicable to this call.")

def create_draft_notes(
    class_material_files: list[str] | None = None,
    model: str = DEFAULT_MODEL,
) -> ChapterNotes:
    """
    Takes a prepared study material string and generates a single self-contained note set
    for the chapter. All content lives in one unified markdown string — no index file or
    sub-topic objects are created.

    Args:
        class_material_str (str | None): Prepared class material as a string (e.g., PDF or image converted to markdown). Defaults to None.
        class_material_files (list[str] | None): List of file paths to class material. Defaults to None.
        model (str): The AI model to use for generation. Defaults to DEFAULT_MODEL.

    Full Return (str):
        A single markdown string containing the complete note set for the chapter,
        including the AI's thought process and the organized notes.

    Return:
        str: One unified note set for the entire chapter as a markdown string.
    """

    return call_llm(
        prompt = "",
        prompt_filepaths=class_material_files,
        system_prompt_filepath="prompt_templates/create_draft_notes_prompt.md",
        return_class = ChapterNotes,
        )


#
# Function and custom types to create sample Q&A 
# 
class QAPair(BaseModel):
    question: str
    answer: str
    question_type: str

class QAList(BaseModel):
    pairs: list[QAPair]

def create_sample_test_qa(
    list_of_question_types: list[str],
    class_material_files: list[str] | None = None,
    number_of_questions: int = 10,
    model: str = DEFAULT_MODEL,
) -> QAList:
    """
    Takes class material and generates a list of questions and answers
    of a specified type, drawn from the entire material.

    Args:
        question_type (str): The type of questions to generate, e.g.:
            Definitional, Conceptual, Computational, Application,
            Compare/Contrast, True/False, Short Answer,
            Process/Sequence, Analysis, MCQ, Open-Ended.
        class_material_str (str | None): Prepared class material as a string. Defaults to None.
        class_material_files (list[str] | None): List of file paths to class material. Defaults to None.
        number_of_questions (int): Number of Q&A pairs to generate. Defaults to 10.
        model (str): The AI model to use for generation. Defaults to DEFAULT_MODEL.

    Full Return (QAList):
        pairs (list[QAPair]): A list of question-answer pairs, each containing:
            - question (str): The question text.
            - answer (str): The answer text.
            - question_type (str): The type of question as specified.

    Return:
        QAList: A single self-contained set of Q&A pairs for the entire class material.

    """

    return call_llm(
            prompt="",
            prompt_filepaths=class_material_files,
            system_prompt_filepath="prompt_templates/create_testing_qa_prompt.md",
            return_class=QAList,
            model=model,
            question_types=", ".join(list_of_question_types),
            number_of_questions=number_of_questions,
        )


#
# Function to check answers 
#
class CheckAnswerResponse(BaseModel):
    rubric: list[str] = Field(description="The question-specific criteria derived from the true answer.")
    evaluation: str = Field(description="How the student answer performs against each rubric criterion.")
    analysis: str = Field(description="Explanation of why the student answer is right or wrong. ")
    result: bool = Field(description="True if all criteria are satisfied, False otherwise.")

def check_answer(
    question: str,
    true_answer: str,
    student_answer: str,
    model: str = DEFAULT_MODEL,
) -> CheckAnswerResponse:
    """
    Takes a question, the true answer, and the student's answer, then evaluates
    correctness with structured reasoning — returning a True/False result plus
    the full reasoning chain behind it.

    Args:
        question (str): The question that was asked.
        true_answer (str): The correct answer to evaluate against.
        student_answer (str): The student's submitted answer.
        model (str): The AI model to use for evaluation. Defaults to DEFAULT_MODEL.

    Full Return (CheckAnswerResponse):
        rubric (list[str]): Question-specific criteria derived from the true answer.
        evaluation (str): How the student's answer performs against each rubric criterion.
        analysis (str): Why the student's answer is right or wrong.
        result (bool): True if all criteria are satisfied, False otherwise.

    Return:
        CheckAnswerResponse: The full evaluation object including reasoning and final T/F result.
    """

    return call_llm(
            prompt=f"Question: {question}\n\n True Answer: {true_answer}\n\n Student Answer: {student_answer}",
            prompt_filepaths=[],
            system_prompt_filepath="prompt_templates/check_answer_prompt.md",
            return_class=CheckAnswerResponse,
            model=model,
        )


#
# Function to create learning goals 
#
 #come back to goal
class Goal(BaseModel):
    goal: str = Field(description="Full goal statement.")
    goal_description: str = Field(description="Show description of the goal.")

class GoalList(BaseModel):
    thought_process: str = Field(description="Structured reasoning covering candidate concepts, inclusion/exclusion decisions, cognitive level mapping, and final ordering rationale.")
    goals: list[Goal] = Field(description="Learning goals.")
    
def create_study_goals(
    class_material_files: list[str] | None = None,
    model: str = DEFAULT_MODEL,
) -> GoalList:
    """
    Takes class material and generates a structured set of learning goals for a study session.
    Goals are cognitively leveled (recall, application, synthesis), ordered by logical dependency,
    and designed to drive Socratic questioning — each goal targets a demonstrable competency,
    not a memorizable procedure.

    Args:
        class_material_str (str | None): Class material as a plain string. Defaults to None.
        class_material_files (list[str] | None): File paths to class material (text, PDF, images, slides). Defaults to None.
        model (str): The model to use for generation. Defaults to DEFAULT_MODEL.

    Returns:
        GoalList:
            thought_process (str): Structured reasoning over candidate concepts, inclusions/exclusions,
                cognitive level assignments, and final goal ordering.
            session_title (str): Short title derived from the material.
            goals (list[Goal]): 3–7 learning goals, each containing:
                - id (int): Sequential position in logical dependency order.
                - verb (str): Single lowercase action verb (e.g. 'distinguish', 'derive', 'compare').
                - goal (str): Measurable goal statement from the student's perspective.
                - goal_description (str): A misconception or boundary condition that distinguishes
                  real understanding from surface memorization.
    """

    return call_llm(
            prompt="",
            prompt_filepaths=class_material_files,
            system_prompt_filepath="prompt_templates/create_learning_goals_prompt.md",
            return_class=GoalList,
            model=model,
        )


#
# Function to create socratic questions
#

class SocraticQAPair(BaseModel):
    question: str
    answer: str

class SocraticQAList(BaseModel):
    goal: str  # the overarching learning objective this Q&A set is trying to teach
    pairs: list[SocraticQAPair]

def create_socratic_qa(
    goal: str,    # learning_goal.goal
    goal_description: str,  # learning_goal.goal_description
    class_material_files: list[str] | None = None,
    model: str = DEFAULT_MODEL,
) -> SocraticQAList:
    """
    Takes class material and generates a list of Socratic questions and answers
    designed to teach and guide understanding — not to test recall.
    Questions lead the student to discover concepts through reasoning and inquiry.

    Args:
        question_type (str): The type of questions to generate.
        class_material_str (str | None): Prepared class material as a string. Defaults to None.
        class_material_files (list[str] | None): List of file paths to class material. Defaults to None.
        number_of_questions (int): Number of Socratic Q&A pairs to generate. Defaults to 10.
        model (str): The AI model to use for generation. Defaults to DEFAULT_MODEL.

    Full Return (SocraticQAList):
        goal (str): The overarching learning objective this Q&A set is trying to teach.
        pairs (list[SocraticQAPair]): A list of Socratic question-answer pairs, each containing:
            - question (str): A guiding question designed to lead the student to understanding.
            - answer (str): The answer the question is meant to guide the student toward.
            - question_type (str): The type of question as specified.

    Return:
        SocraticQAList: A single self-contained set of Socratic Q&A pairs with an overarching goal.
    """


    return call_llm(
            prompt="",
            prompt_filepaths=class_material_files,
            system_prompt_filepath="prompt_templates/create_socratic_qa_prompt.md",
            return_class=SocraticQAList,
            goal = goal,
            goal_description = goal_description,
            model=model,
        )


#
# Function to create follow-up Testing Questions
#

class QAPair(BaseModel):
    question: str
    answer: str
    question_type: str

class QAList(BaseModel):
    pairs: list[QAPair]

def create_followup_testing_qa(
    question: str,
    student_answer: str,
    true_answer: str,
    evaluation: str,
    list_of_question_types: list[str],
    number_of_questions: int = 5,
    class_material_files: list[str] | None = None,
    model: str = DEFAULT_MODEL,
) -> QAList:
    """
    Takes a question the student got wrong and generates follow-up questions
    designed to probe the revealed knowledge gap more deeply.
    Triggered when check_answer returns result=False.

    Args:
        question (str): The original question the student got wrong.
        student_answer (str): The student's incorrect answer.
        true_answer (str): The correct answer.
        evaluation (str): The evaluation from check_answer explaining where the student went wrong.
        number_of_questions (int): The number of follow-up questions to generate. Defaults to 5.
        class_material_files (list[str] | None): List of file paths to class material. Defaults to None.
        model (str): The AI model to use for generation. Defaults to DEFAULT_MODEL.

    Full Return (QAList):
        pairs (list[QAPair]): A list of follow-up Q&A pairs, each containing:
            - question (str): A follow-up question targeting the student's specific gap.
            - answer (str): The complete, self-contained answer to the question.
            - question_type (str): The format of the question as specified.

    Return:
        QAList: A set of follow-up Q&A pairs targeting the student's specific misunderstanding.
    """

    return call_llm(
            prompt=(
                f"Original question: {question}\n\n"
                f"Correct answer: {true_answer}\n\n"
                f"Student answer: {student_answer}\n\n"
                f"Evaluation: {evaluation}"
            ),
            prompt_filepaths=class_material_files or [],
            system_prompt_filepath="prompt_templates/create_followup_testing_qa_prompt.md",
            return_class=QAList,
            model=model,
            question_types=", ".join(list_of_question_types),
            number_of_questions=number_of_questions,
        )


#
# Function to update notes
#
def update_notes(
    notes: str,
    missed_items: list[dict],
    class_material_files: list[str] | None = None,
    model: str = DEFAULT_MODEL,
) -> ChapterNotes:
    """
    Takes the existing chapter notes and patches them for every question the student
    got wrong in a single LLM call.

    Args:
        notes (str): The existing chapter note set as a markdown string from create_draft_notes.
        missed_items (list[dict]): List of dicts, each with keys:
            question, student_answer, true_answer, evaluation.
        class_material_files (list[str] | None): List of file paths to class material. Defaults to None.
        model (str): The AI model to use for generation. Defaults to DEFAULT_MODEL.

    Return:
        ChapterNotes: The updated note set with all gaps addressed.
    """
    items_text = ""
    for i, item in enumerate(missed_items, 1):
        items_text += (
            f"--- Missed Question {i} ---\n"
            f"Question: {item['question']}\n"
            f"True Answer: {item['true_answer']}\n"
            f"Student Wrong Answer: {item['student_answer']}\n"
            f"Analysis of Why Incorrect: {item['evaluation']}\n\n"
        )

    return call_llm(
        prompt=(
            f"{items_text}"
            f"Relevant Notes:\n{notes}"
        ),
        prompt_filepaths=class_material_files or [],
        system_prompt_filepath="prompt_templates/patch_relevant_notes_prompt.md",
        return_class=ChapterNotes,
        model=model,
    )