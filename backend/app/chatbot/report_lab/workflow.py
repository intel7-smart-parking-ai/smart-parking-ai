"""LangGraph orchestration. Business data stays outside callback state."""
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import perf_counter
from typing import TypedDict

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from . import alerts, fees, planner, prompts, reporter, revenue, spaces, vehicle, policies
from .query import PAYMENT_STATUS_PATTERN, Clarification, parse as parse_query
from .permissions import PUBLIC_TASKS, PermissionDenied, require_task

logger = logging.getLogger(__name__)
WORKERS = {"revenue": revenue.fetch, "alerts": alerts.fetch,
           "spaces": spaces.fetch, "fees": fees.fetch,
           "vehicle": vehicle.fetch, "policies": policies.fetch}


class ReportFailure(RuntimeError):
    def __init__(self, agent, trace_id, prompt_versions, error_type):
        super().__init__("Report stage failed")
        self.agent = agent
        self.trace_id = trace_id
        self.prompt_versions = dict(prompt_versions)
        self.error_type = error_type


class State(TypedDict, total=False):
    tasks: list[str]
    revenue_status: str
    alerts_status: str
    spaces_status: str
    fees_status: str
    vehicle_status: str
    policies_status: str


def report(question, query=None, *, allowed_tasks=PUBLIC_TASKS):
    query = query or parse_query(question)
    day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    trace = []
    results = {}
    cards = []
    prompt_versions = {}
    lock = Lock()
    vehicle_payment_lookup = query.individual_fee and bool(query.vehicle_id) and bool(
        re.search(PAYMENT_STATUS_PATTERN, question)
    )
    use_llm = bool(os.getenv("REPORT_OPENROUTER_KEY")) and not vehicle_payment_lookup
    client = None
    handler = None
    config = {"run_name": "parkon-report"}
    if os.getenv("REPORT_LANGFUSE") == "1":
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler
        if not all(os.getenv(key) and not os.getenv(key).endswith("...") for key in
                   ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")):
            raise ValueError("Langfuse project credentials are required")
        client = get_client()
        handler = CallbackHandler()
        config["callbacks"] = [handler]

    def managed(role):
        prompt = prompts.load(role, client)
        with lock:
            prompt_versions[role] = prompt.metadata()
        return prompt

    @contextmanager
    def step(role, prompt):
        start = perf_counter()
        row = {"stage": role, "status": "ok", **prompt.metadata()}
        try:
            yield row
        except Clarification:
            row["status"] = "clarification"
            raise
        except Exception:
            row["status"] = "error"
            raise
        finally:
            row["ms"] = round((perf_counter() - start) * 1000, 1)
            with lock:
                trace.append(row)

    def plan(_state, config):
        prompt = managed("planner")

        def execute(_input):
            with step("planner", prompt) as execution:
                if vehicle_payment_lookup:
                    execution["execution"] = "rules"
                    return {"tasks": ["vehicle", "fees"]}
                if query.vehicle_id and not query.individual_fee and (
                    re.fullmatch(r"\d{4} 차량 위치", question)
                    or query.full_plate and re.search(r"위치|어디|찾", question)
                ):
                    execution["execution"] = "rules"
                    return {"tasks": ["vehicle"]}
                try:
                    try:
                        tasks = planner.plan(question, prompt.compile())
                    except Exception:
                        tasks = planner.plan(question, prompt.compile(), use_llm=False)
                        execution["routing_fallback"] = "validated-topic-rules"
                    # A validated catalog topic must not disappear when an LLM
                    # omits the policy worker from an otherwise valid plan.
                    if query.topics and "policies" not in tasks:
                        tasks.append("policies")
                    if query.charging_status and "spaces" not in tasks:
                        tasks.append("spaces")
                    support_topics = {'payment_help','exit_rules','lookup_help','discount_steps','walking','ev_usage','lost_found','vehicle_rules','passes'}
                    if (any(key in support_topics for key, _ in query.topics) and not query.vehicle_id
                            and not query.individual_fee and not re.search(r'빈자리|빈 자리|매출|경보|기본\s*요금|요금표|요금.{0,6}(?:얼마|조회)|현재\s*(?:요금|결제)', question)):
                        tasks = ['policies']
                    if ((query.individual_fee or query.vehicle_id) and "fees" in tasks
                            or query.vehicle_id and "policies" in tasks) and "vehicle" not in tasks:
                        tasks.append("vehicle")
                    return {"tasks": [task for task in planner.TASKS if task in tasks]}
                except Exception as exc:
                    raise RuntimeError("Planner request failed: " + type(exc).__name__) from None

        return RunnableLambda(execute).invoke({}, config={
            **config, "run_name": "planner-prompt", "metadata": prompt.metadata()})

    def worker(task, fetch):
        def run(_state, config):
            prompt = managed(task)

            def execute(_input):
                try:
                    with step(task, prompt) as execution:
                        require_task(task, allowed_tasks)
                        if query.scope_problem:
                            raise Clarification(query.scope_problem)
                        values = fetch(day, query)
                        from importlib import import_module
                        from .query import core
                        facts = import_module(core()[0].__package__ + ".answer_format").facts(values)
                        if task == "spaces":
                            facts = spaces.format_values(values, query)
                        result = {"values": values, "summary": prompt.compile(facts=facts)}
                        if getattr(values, "missing", False):
                            execution["status"] = "unavailable"
                            result["unavailable"] = True
                            result["summary"] = "\n".join(f"{key} {value}" for key, value in values.items())
                    status = "unavailable" if result.get("unavailable") else "ok"
                except PermissionDenied as exc:
                    result = {"error": str(exc), "permission_denied": True}
                    status = "denied"
                except Clarification as exc:
                    result = {"summary": str(exc), "needs_clarification": True}
                    status = "clarification"
                except Exception:
                    result = {"error": "서버에서 이 항목을 확인하지 못했어요."}
                    status = "error"
                with lock:
                    results[task] = result
                return {f"{task}_status": status}

            return RunnableLambda(execute).invoke({}, config={
                **config, "run_name": task + "-response", "metadata": prompt.metadata()})
        return run

    def render(state, config):
        prompt = managed("reporter")

        def execute(_input):
            with step("reporter", prompt):
                cards.extend(reporter.render(state["tasks"], results))
                completed = sum("values" in results[task] and not results[task].get("unavailable") for task in state["tasks"])
                summary = prompt.compile(completed=completed, failed=len(state["tasks"]) - completed)
                results["reporter"] = {"summary": summary}
            return {}

        return RunnableLambda(execute).invoke({}, config={
            **config, "run_name": "reporter-response", "metadata": prompt.metadata()})

    graph = StateGraph(State)
    graph.add_node("planner", plan)
    graph.add_node("reporter", render)
    graph.add_edge(START, "planner")
    for name, fetch in WORKERS.items():
        graph.add_node(name, worker(name, fetch))
        if name != "vehicle":
            graph.add_edge(name, "reporter")
    graph.add_conditional_edges("planner", lambda state: ["vehicle"] if "vehicle" in state["tasks"] else state["tasks"] or ["reporter"],
                                list(WORKERS) + ["reporter"])
    graph.add_conditional_edges("vehicle", lambda state: [task for task in state["tasks"] if task != "vehicle"] or ["reporter"],
                                [name for name in WORKERS if name != "vehicle"] + ["reporter"])
    graph.add_edge("reporter", END)
    try:
        state = graph.compile().invoke({}, config=config)
    except Exception as exc:
        failed = next((row["stage"] for row in reversed(trace) if row["status"] == "error"), "execution")
        trace_id = handler.last_trace_id if handler else None
        raise ReportFailure(failed, trace_id, prompt_versions, type(exc).__name__) from None
    trace_id = handler.last_trace_id if handler else None
    if client and trace_id:
        for row in trace:
            try:
                client.create_score(trace_id=trace_id, name=row["stage"] + "_execution_success",
                                    value=float(row["status"] == "ok"), data_type="NUMERIC")
            except Exception:
                logger.warning("Langfuse execution score could not be queued")
    return {"date": day, "source": "api" if os.getenv("REPORT_API_BASE") else "sample",
            "planner": "llm" if use_llm else "rules", "orchestrator": "langgraph",
            "langfuse_configured": client is not None, "trace_id": trace_id,
            "tasks": state["tasks"], "prompt_versions": prompt_versions,
            "summary": results["reporter"]["summary"],
            "clarification": None if state["tasks"] else
                "빈자리, 차량 위치, 차량 요금, 이용 정책, 오늘 매출, 경보 중 어떤 정보를 확인할까요? 결제 실행은 기존 확인 절차에서 진행해 주세요.",
            "cards": cards, "trace": trace}
