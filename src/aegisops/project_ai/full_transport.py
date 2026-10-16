"""Fixed full-check explanation profile; only closed saved summaries cross the wire."""

import json

from pydantic import SecretStr

from aegisops.project_ai import transport
from aegisops.project_ai.full_models import FullSummary
from aegisops.project_ai.review_models import ReviewExplanation


def explain_full(summary: FullSummary, key: SecretStr) -> tuple[ReviewExplanation, int, int]:
    try:
        summary = FullSummary.model_validate(summary)
        instructions = (
            "Ты объясняешь сводку полной проверки на общем снимке на русском языке. "
            "Ты не видел исходники, имена, файлы, сообщения инструментов или логи проекта. "
            "Снимок подтверждает только связанный захваченный ввод, а не полное исполнение. "
            "Состояние запуска и область исполнения указаны явно. Не делай общего вывода, "
            "что проект протестирован, исправлен, безопасен или совпадает с текущей папкой. "
            "Если статус incomplete, прямо скажи об этом и упомяни последовательный захват "
            "и выбранную область проверки. Ты можешь приоритизировать только те категории, "
            "которые присутствуют во входной сводке. Не предлагай исполнение команд. "
            "Дай краткий разбор, не более пяти приоритетных действий, их обоснование и "
            "следующий шаг для ручной проверки. Коды действий должны присутствовать именно "
            "в categories входной сводки; если categories пуст, actions должен быть пуст. "
            "Учитывай все ограничения limitations. Возврати только JSON по схеме: "
            + json.dumps(ReviewExplanation.model_json_schema(), ensure_ascii=False)
        )
        content, input_tokens, output_tokens = transport._exchange(
            summary.model_dump_json(), instructions, key
        )
        result = ReviewExplanation.model_validate(content)
        allowed = {item.code for item in summary.categories}
        if any(item.code not in allowed for item in result.actions):
            raise transport.AnalysisError()
        return result, input_tokens, output_tokens
    except Exception:
        raise transport.AnalysisError() from None


__all__ = ("explain_full",)
