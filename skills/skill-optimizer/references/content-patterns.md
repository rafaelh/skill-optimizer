# Content patterns for skill bodies

Once a skill activates, its full `SKILL.md` body loads into the agent's context window. Every token competes with conversation history, system context, and other active skills. These patterns spend that context well.

## Spending context wisely

### Add what the agent lacks; omit what it knows

Focus on project-specific conventions, domain-specific procedures, non-obvious edge cases, and the particular tools or APIs to use. Skip generic education.

````markdown
<!-- Too verbose — the agent already knows what PDFs are -->
## Extract PDF text

PDF (Portable Document Format) files are a common file format that contains
text, images, and other content. To extract text from a PDF, you'll need to
use a library. pdfplumber is recommended because it handles most cases well.

<!-- Better — jumps straight to what the agent wouldn't know -->
## Extract PDF text

Use pdfplumber for text extraction. For scanned documents, fall back to
pdf2image with pytesseract.

```python
import pdfplumber

with pdfplumber.open("file.pdf") as pdf:
    text = pdf.pages[0].extract_text()
```
````

Test for each paragraph: "Would the agent get this wrong without this instruction?" If no, cut it.

### Design coherent units

Skills should encapsulate one coherent unit of work. Too narrow → multiple skills load for one task, with overhead and conflicting instructions. Too broad → hard to activate precisely. "Query a database and format the results" is one unit; adding "and administer the database" is two.

### Aim for moderate detail

Exhaustive skills hurt: the agent struggles to extract what's relevant and may pursue irrelevant instructions. Concise stepwise guidance with one working example tends to outperform exhaustive documentation.

### Progressive disclosure

Keep `SKILL.md` under 500 lines / 5000 tokens. Move detail into:

- `references/` — reference docs, schemas, edge-case catalogs
- `scripts/` — reusable executables
- `assets/` — templates, lookup tables, images

Critical rule: tell the agent *when* to load each reference. "Read `references/api-errors.md` if the API returns a non-200 status" is useful. "See references/ for details" is dead weight — the agent won't follow it.

## Calibrating control

### Match specificity to fragility

**Give freedom** when multiple approaches are valid and the task tolerates variation. Explain *why* — an agent that understands purpose makes better context-dependent decisions:

```markdown
## Code review process

1. Check all database queries for SQL injection (use parameterized queries)
2. Verify authentication checks on every endpoint
3. Look for race conditions in concurrent code paths
4. Confirm error messages don't leak internal details
```

**Be prescriptive** when operations are fragile or sequencing matters:

````markdown
## Database migration

Run exactly this sequence:

```bash
python scripts/migrate.py --verify --backup
```

Do not modify the command or add additional flags.
````

Most skills mix both. Calibrate each section independently.

### Provide defaults, not menus

````markdown
<!-- Too many options -->
You can use pypdf, pdfplumber, PyMuPDF, or pdf2image...

<!-- Clear default with escape hatch -->
Use pdfplumber for text extraction:

```python
import pdfplumber
```

For scanned PDFs requiring OCR, use pdf2image with pytesseract instead.
````

### Favor procedures over declarations

Teach *how to approach* a class of problems, not *what to produce* for one instance:

```markdown
<!-- Specific answer — only useful for this exact task -->
Join the `orders` table to `customers` on `customer_id`, filter where
`region = 'EMEA'`, and sum the `amount` column.

<!-- Reusable method — works for any analytical query -->
1. Read the schema from `references/schema.yaml` to find relevant tables
2. Join tables using the `_id` foreign key convention
3. Apply any filters from the user's request as WHERE clauses
4. Aggregate numeric columns as needed and format as a markdown table
```

## Reusable structural patterns

### Gotchas section

The highest-value content in many skills. Concrete corrections to mistakes the agent will make without being told otherwise — not generic advice.

```markdown
## Gotchas

- The `users` table uses soft deletes. Queries must include
  `WHERE deleted_at IS NULL` or results will include deactivated accounts.
- The user ID is `user_id` in the database, `uid` in the auth service,
  and `accountId` in the billing API. All three refer to the same value.
- The `/health` endpoint returns 200 as long as the web server is running,
  even if the database connection is down. Use `/ready` to check full
  service health.
```

Keep gotchas in `SKILL.md` itself — the agent must encounter them before hitting the situation. Reference files only work if you describe a specific load trigger, but for non-obvious issues the agent often won't recognize the trigger.

When you correct an agent's mistake during real use, add the correction to gotchas. This is the single most direct way to iterate on a skill.

### Templates for output format

Concrete templates beat prose descriptions because agents pattern-match well against structure. Short templates inline; longer ones in `assets/`.

````markdown
## Report structure

```markdown
# [Analysis Title]

## Executive summary
[One-paragraph overview of key findings]

## Key findings
- Finding 1 with supporting data
- Finding 2 with supporting data

## Recommendations
1. Specific actionable recommendation
2. Specific actionable recommendation
```
````

### Checklists for multi-step workflows

Explicit checklists help the agent track progress and avoid skipping steps.

```markdown
## Form processing workflow

Progress:
- [ ] Step 1: Analyze the form (run `scripts/analyze_form.py`)
- [ ] Step 2: Create field mapping (edit `fields.json`)
- [ ] Step 3: Validate mapping (run `scripts/validate_fields.py`)
- [ ] Step 4: Fill the form (run `scripts/fill_form.py`)
- [ ] Step 5: Verify output (run `scripts/verify_output.py`)
```

### Validation loops

```markdown
## Editing workflow

1. Make your edits
2. Run validation: `python scripts/validate.py output/`
3. If validation fails:
   - Review the error message
   - Fix the issues
   - Run validation again
4. Only proceed when validation passes
```

A reference document can serve as the validator — instruct the agent to check work against it before finalizing.

### Plan-validate-execute

For batch or destructive operations, force the agent through an intermediate plan that gets validated against a source of truth before execution:

```markdown
## PDF form filling

1. Extract form fields: `python scripts/analyze_form.py input.pdf` → `form_fields.json`
2. Create `field_values.json` mapping each field name to its intended value
3. Validate: `python scripts/validate_fields.py form_fields.json field_values.json`
4. If validation fails, revise `field_values.json` and re-validate
5. Fill the form: `python scripts/fill_form.py input.pdf field_values.json output.pdf`
```

The key ingredient is step 3: a script that compares plan against truth and produces a self-correcting error like "Field 'signature_date' not found — available: customer_name, order_total, signature_date_signed".

### Bundling reusable scripts

If you watch the agent reinvent the same logic across runs (chart building, format parsing, validation) — write a tested script and bundle it in `scripts/`. The skill references the script by relative path; the agent invokes it instead of re-deriving the logic.

## How to iterate

The first draft usually needs refinement. Run the skill against real tasks. Read the agent's execution traces, not just final outputs. Common signals:

- Agent tries several approaches before finding one that works → instructions too vague.
- Agent follows instructions that don't apply → instructions over-broad.
- Agent picks an inconsistent default → too many options without a clear preference.

Feed the trace back into the skill and revise. Even one execute-then-revise pass noticeably improves quality.
