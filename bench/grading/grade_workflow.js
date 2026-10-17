export const meta = {
  name: 'blind-grade-cli-bench',
  description: 'Blind-grade the MCP-only and web-only CLI answers against official references (2 judges per batch, then adjudicate disputes)',
  phases: [
    { title: 'Judge', detail: 'two independent judges per batch, blind to which system wrote each answer' },
    { title: 'Adjudicate', detail: 'a third grader settles disagreements and verifies every flagged claim' },
  ],
}

const GRADE = {
  type: 'object',
  properties: {
    answer_id: { type: 'string' },
    main_correct: { type: 'boolean' },
    district_status: { type: 'string', enum: ['correct', 'wrong', 'not_stated', 'not_applicable'] },
    definitive: { type: 'boolean' },
    asked_back: { type: 'boolean' },
    claims_checked: { type: 'integer' },
    claims_unverifiable: { type: 'integer' },
    problems: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          quote: { type: 'string' },
          verdict: { type: 'string', enum: ['wrong', 'outdated'] },
          why: { type: 'string' },
        },
        required: ['quote', 'verdict', 'why'],
      },
    },
  },
  required: ['answer_id', 'main_correct', 'district_status', 'definitive', 'asked_back', 'claims_checked', 'claims_unverifiable', 'problems'],
}
const GRADES = { type: 'object', properties: { grades: { type: 'array', items: GRADE } }, required: ['grades'] }
const DECISIONS = {
  type: 'object',
  properties: {
    decisions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          answer_id: { type: 'string' },
          main_correct: { type: 'boolean' },
          district_status: { type: 'string', enum: ['correct', 'wrong', 'not_stated', 'not_applicable'] },
          confirmed_problems: GRADE.properties.problems,
          rejected_problems: GRADE.properties.problems,
          note: { type: 'string' },
        },
        required: ['answer_id', 'main_correct', 'district_status', 'confirmed_problems', 'rejected_problems'],
      },
    },
  },
  required: ['decisions'],
}

const RUBRIC = `
Grading rubric (apply literally, the same way to every answer):
- The file has "references" (keyed by question_id: the question, truth_main, and official reference data such as calendar_window_official = the official collection schedule for that district around the asked date, district_rules, put_out_by, contact, holiday_policy; for Onagawa town also gyoseiku_candidates, calendar windows per 行政区, year_end, and sorting_rules_official_with_validity with valid_from/valid_to) and "answers" (answer_id, question_id, answer text in Japanese).
- main_correct: the answer commits to the main answer and it matches truth_main.
  * Kanazawa (question ids c01..c28): the set of garbage categories the answer says can be put out ON THE ASKED DATE must equal truth_main.collections (paraphrases are fine, e.g. 「資源ごみ（あき缶・ペットボトル・プラスチック資源など）」 = 「資源 あき缶・ペットボトル プラスチック資源など」; 「資源 プラスチック資源」 is a different category). Missing or extra categories => false. If truth_main.collections is empty, the answer must say nothing is collected that day.
  * Onagawa date questions (O1..O6): the categories for the asked date must match truth_main. If the answer gives the right items but conditions them on an unresolved 行政区 and every condition gives the same items, it still counts as committing.
  * Onagawa sorting questions (S1..S4): the destination (basket/category) for the item AS OF THE ASKED DATE must match truth_main.
  * If the answer refuses, only asks back, or gives several alternatives without committing to one, main_correct=false and definitive=false.
- district_status (Kanazawa only; use not_applicable for Onagawa): correct if the district/地区 it names equals truth_main.district, wrong if it names another, not_stated if it names none.
- definitive: the answer commits to a main answer (hedges like 「念のため確認を」 do not make it non-definitive). asked_back: it asks the user for more information.
- Claims: every OTHER concrete, checkable factual statement in the answer (dates and items of other collection days, next/previous collection dates, weekday rules like 第2・4木曜日, year-end last day / new-year first day, put-out deadline time, contact phone numbers, holiday collection statements, sorting destinations and rules, statements about when rules changed). Count them in claims_checked. Count in claims_unverifiable those the reference cannot confirm or refute (e.g. sorting details for Kanazawa, collection-point locations, URLs). Advice, hedges and source titles are not claims.
- problems: list ONLY claims that the reference contradicts. verdict "outdated" when the claim is a rule that was valid in a different period than the asked date (e.g. Onagawa sorting rules valid only until 2026-09-30 stated as applying to a date after 2026-10-01), otherwise "wrong". Quote the exact Japanese text (<= 60 chars) and explain in one sentence citing the reference. Do not flag a claim just because the reference does not mention it (that is unverifiable). Do not flag wording differences when the meaning matches the reference. Check dates carefully against calendar_window_official (dates outside the window are unverifiable unless rules make them certain).
- Judge each answer on its own merits. You are not told which system wrote which answer; do not guess, and do not let style influence the grade.`

const judgePrompt = (b, k) => `You are grader #${k + 1} for a blind evaluation of Japanese household-garbage answers. Read the JSON file ${b.path} with the Read tool (read it completely, using offset/limit if needed). Do not open any other file or directory.
${RUBRIC}
Return one grade per answer in the file (${b.n} answers), with answer_id exactly as given.`

const adjPrompt = (b, disputes) => `You are the adjudicator for a blind evaluation of Japanese household-garbage answers. Read the JSON file ${b.path} with the Read tool (read it completely, using offset/limit if needed). Do not open any other file or directory.
${RUBRIC}
Two independent graders graded every answer. For the answers below they disagreed on main_correct or district_status, and/or at least one of them flagged problems. For each listed answer_id, re-read the answer and its reference yourself and decide: main_correct, district_status, and for EVERY flagged problem (from either grader) whether it is real (put it in confirmed_problems, verdict wrong/outdated) or not (rejected_problems, with why). Be skeptical of flagged problems: confirm one only if the reference clearly contradicts the quoted claim. You may also add a problem neither grader found, if the reference clearly contradicts it.
Disputed items:
${JSON.stringify(disputes, null, 1)}`

const key = (g) => `${g.main_correct}|${g.district_status}`

const results = await pipeline(
  args.batches,
  (b) => parallel([0, 1].map((k) => () => agent(judgePrompt(b, k), { label: `judge:${b.name}:${k + 1}`, phase: 'Judge', schema: GRADES }))),
  async (pair, b) => {
    const [j1, j2] = pair.map((p) => (p ? Object.fromEntries(p.grades.map((g) => [g.answer_id, g])) : null))
    if (!j1 || !j2) { log(`${b.name}: a judge failed; adjudicating everything`) }
    const ids = [...new Set([...Object.keys(j1 || {}), ...Object.keys(j2 || {})])]
    const disputes = []
    for (const id of ids) {
      const a = j1 && j1[id], c = j2 && j2[id]
      const disagree = !a || !c || key(a) !== key(c)
      const flagged = (a && a.problems.length) || (c && c.problems.length)
      if (disagree || flagged) disputes.push({ answer_id: id, grader1: a || null, grader2: c || null })
    }
    log(`${b.name}: ${disputes.length} of ${ids.length} answers need adjudication`)
    if (!disputes.length) return { batch: b.name, judges: pair, decisions: [] }
    const d = await agent(adjPrompt(b, disputes), { label: `adjudicate:${b.name}`, phase: 'Adjudicate', schema: DECISIONS })
    return { batch: b.name, judges: pair, disputes: disputes.map((x) => x.answer_id), decisions: d ? d.decisions : null }
  },
)
return results
