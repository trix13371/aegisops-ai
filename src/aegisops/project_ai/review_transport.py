"""Fixed combined explanation profile; only closed saved-check summaries cross the wire."""

import json

from pydantic import SecretStr

from aegisops.project_ai import transport
from aegisops.project_ai.review_models import ReviewExplanation, ReviewSummary


def explain_review(summary: ReviewSummary, key: SecretStr) -> tuple[ReviewExplanation, int, int]:
    try:
        summary = ReviewSummary.model_validate(summary)
        instructions = (
            "Ты объясняешь сводку выбранных сохранённых проверок на русском языке. "
            "Ты не видел исходники, имена, файлы, сообщения инструментов или логи проекта. "
            "Статическая проверка и запуск тестов независимы: их соответствие одной версии "
            "исходников и текущей папке не доказано. Не делай общего вывода, что проект "
            "протестирован, исправлен или безопасен. Прошедшие инструменты подтверждают "
            "только свой ограниченный профиль. Не выдавай неподтверждённый, частичный, "
            "усечённый или отсутствующий результат за успех. Не предлагай исполнение команд. "
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
