---
article_id: null
status: "draft"
created_at: "2026-03-23T09:00:00+00:00"
updated_at: "2026-03-23T09:00:00+00:00"
scheduled_publish_at: null
moderation_comment: null
title: "Почему одобренная заявка комика — это еще не место в лайнапе"
slug: "approved-application-not-lineup"
cover_path: "publications/InComedy/post-assets/approved_application_not_lineup_cover.png"
payload_path: ".codex-local/payloads/approved-application-not-lineup.json"
source_refs:
  - "/Users/abetirov/AndroidStudioProjects/InComedy/docs/context/governance/decisions-log/decisions-log-part-05.md"
  - "/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/lineup/ComedianApplicationsService.kt"
  - "/Users/abetirov/AndroidStudioProjects/InComedy/domain/lineup/src/commonMain/kotlin/com/bam/incomedy/domain/lineup/LineupManagementService.kt"
attached_links:
  - "https://github.com/returnNnull/InComedy"
  - "https://github.com/returnNnull/InComedy/tree/main/server/src/main/kotlin/com/bam/incomedy/server/lineup"
  - "https://github.com/returnNnull/InComedy/tree/main/feature/lineup"
last_synced_at: null
publish_strategy: null
last_error: null
---
# Почему одобренная заявка комика — это еще не место в лайнапе

Когда в продукте появляется отбор комиков и управление программой вечера, очень легко склеить эти вещи в одну сущность. Одобрил заявку, значит человек уже стоит в списке выступающих. На первый взгляд это даже удобно: меньше таблиц, меньше переходов, меньше кода.

В `InComedy` пошли не по этому пути. Здесь одобренная заявка не становится “готовым лайнапом” автоматически. Она только один раз материализуется в отдельную черновую запись лайнапа с явным порядком. Это решение зафиксировано в [D-067](/Users/abetirov/AndroidStudioProjects/InComedy/docs/context/governance/decisions-log/decisions-log-part-05.md) и проходит через весь новый контур: от серверного хранения и API до общего KMP-сервиса `[LineupManagementService](/Users/abetirov/AndroidStudioProjects/InComedy/domain/lineup/src/commonMain/kotlin/com/bam/incomedy/domain/lineup/LineupManagementService.kt)`.

Главная мысль здесь простая: решение организатора “этого комика можно брать” и решение “в каком порядке он выйдет на сцену” — это разные действия с разными рисками.

## Что именно разделили в коде

Сначала в проекте появился отдельный контур заявок комиков:

- backend migration `[V13__comedian_applications_foundation.sql](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/resources/db/migration/V13__comedian_applications_foundation.sql)`;
- сервис подачи и ревью заявок `[ComedianApplicationsService](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/lineup/ComedianApplicationsService.kt)`;
- HTTP-маршруты `[ComedianApplicationsRoutes.kt](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/lineup/ComedianApplicationsRoutes.kt)` для `submit`, `list` и `status change`;
- регрессионные тесты `[ComedianApplicationsRoutesTest](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/test/kotlin/com/bam/incomedy/server/lineup/ComedianApplicationsRoutesTest.kt)`.

Следом появился уже другой контур — лайнап:

- migration `[V14__lineup_entries_foundation.sql](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/resources/db/migration/V14__lineup_entries_foundation.sql)`;
- persistence-контракт `[LineupRepository](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/db/LineupRepository.kt)`;
- сервис управления лайнапом для организатора и ведущего `[LineupService](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/lineup/LineupService.kt)`;
- HTTP-маршруты `[LineupRoutes.kt](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/lineup/LineupRoutes.kt)` для просмотра и перестановки;
- отдельный общий KMP-контур `domain/data/feature/shared lineup`.

Это важный архитектурный сигнал. Лайнап здесь не является еще одним полем у заявки. Он живет как отдельная рабочая модель, у которой есть свой порядок, свой жизненный статус и свои будущие сценические правила.

## Почему `approved` создает только черновой слот

Ключевая точка находится в `ComedianApplicationsService.updateApplicationStatus(...)`: после ревью сервис вызывает `LineupService.ensureApprovedApplicationEntry(...)`. Но этот вызов делает только одну вещь: если заявка стала `approved`, а записи в лайнапе еще нет, создается одна `draft`-запись.

Это не мелкая техническая деталь, а продуктовая граница.

Если бы `approved` сразу означал “человек поставлен в финальную программу”, проект очень быстро смешал бы в одну операцию:

- само решение о допуске;
- факт попадания в вечерний состав;
- позицию в порядке выхода;
- будущие сценические статусы вроде `up_next`, `on_stage`, `done`;
- возможные ручные правки организатора после ревью.

В такой схеме любое обратное движение по заявке начинает ломать уже не только историю решения по заявке, но и саму программу вечера.

Поэтому в `D-067` зафиксирован более безопасный переход: `approved` материализует черновой слот один раз, а обратная синхронизация, удаление и перестройка лайнапа сознательно не включены в этот срез. Это позволяет доставить рабочий контур без разрушительных побочных эффектов.

## Зачем лайнапу явный `order_index`

Второй сильный шаг — хранить порядок выступлений не “как получится”, а явно.

В `[lineup_entries](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/resources/db/migration/V14__lineup_entries_foundation.sql)` сразу зафиксированы:

- `UNIQUE (event_id, order_index)`;
- `UNIQUE (event_id, application_id)`.

Первая гарантия означает: внутри одного события не может быть двух одинаковых позиций. Вторая означает: одна одобренная заявка не может тихо породить несколько слотов.

Дальше `[PostgresLineupRepository.createLineupEntry(...)](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/db/PostgresLineupRepository.kt)` не угадывает порядок по времени создания, а берет следующий явный индекс. А `[LineupService.reorderEventLineup(...)](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/lineup/LineupService.kt)` вообще не принимает частичную перестановку: request должен содержать весь текущий состав и непрерывную последовательность позиций.

Это жестче, чем “давайте просто двигать один элемент вверх-вниз”, но у такого правила есть практический смысл:

- меньше скрытой магии при reorder;
- ниже риск поломать порядок при конкурентных изменениях;
- проще держать согласованность между экраном организатора, режимом ведущего и будущим сценическим состоянием;
- легче тестировать инварианты, а не только happy path.

## Почему это решение влияет на весь следующий продукт

После серверной основы проект сразу вынес этот контур в отдельный общий слой:

- доменные модели и статусы — `[LineupManagementService.kt](/Users/abetirov/AndroidStudioProjects/InComedy/domain/lineup/src/commonMain/kotlin/com/bam/incomedy/domain/lineup/LineupManagementService.kt)`;
- backend-адаптер — `[LineupBackendApi.kt](/Users/abetirov/AndroidStudioProjects/InComedy/data/lineup/src/commonMain/kotlin/com/bam/incomedy/data/lineup/backend/LineupBackendApi.kt)`;
- общий state и orchestration — `[LineupState](/Users/abetirov/AndroidStudioProjects/InComedy/feature/lineup/src/commonMain/kotlin/com/bam/incomedy/feature/lineup/LineupState.kt)`, `[LineupIntent](/Users/abetirov/AndroidStudioProjects/InComedy/feature/lineup/src/commonMain/kotlin/com/bam/incomedy/feature/lineup/LineupIntent.kt)` и `[LineupViewModel](/Users/abetirov/AndroidStudioProjects/InComedy/feature/lineup/src/commonMain/kotlin/com/bam/incomedy/feature/lineup/LineupViewModel.kt)`;
- Swift-friendly boundary — `[LineupBridge](/Users/abetirov/AndroidStudioProjects/InComedy/shared/src/commonMain/kotlin/com/bam/incomedy/shared/lineup/LineupBridge.kt)`.

Именно здесь видно, что решение было не локальной заплаткой под сервер. Если заявка и лайнап разведены правильно, один и тот же контур потом можно одинаково отдать organizer-поверхности, режиму ведущего и будущему сценическому режиму. Если бы эта граница была смазана с самого начала, общий слой унаследовал бы ту же путаницу.

Отдельно показательно, что в серверной модели `[LineupEntryStatus](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/db/LineupRepository.kt)` уже зарезервированы `up_next`, `on_stage`, `done`, `delayed`, `dropped`, хотя текущий MVP использует только `draft`. Это означает важную инженерную дисциплину: будущие состояния учитываются заранее, но не втаскиваются в активный продукт до тех пор, пока не появится отдельный рабочий сценарий.

## Какие риски это решение снимает

Если смотреть на этот контур как на внедрение, а не как на абстрактную архитектуру, он убирает четыре типичных проблемы.

Первая проблема — разрушительная обратная синхронизация. Если при каждом изменении review-статуса автоматически удалять или пересобирать лайнап, можно потерять ручной порядок, заметки и будущие organizer-правки.

Вторая — неявный порядок. Когда позиция определяется временем создания или текущим массивом в UI, система плохо переживает reorder, повторные запросы и несколько клиентов.

Третья — смешение истории отбора и сценического состояния. Заявка отвечает на вопрос “берем или не берем”, а лайнап отвечает на вопрос “кто, когда и в каком статусе идет на сцену”. Это разные вопросы.

Четвертая — дорогой рост следующего функционала. Live-stage, объявления “next up”, статистика выступлений и ручные organizer-заметки гораздо проще развивать поверх отдельного `lineup entry`, чем поверх раздутой заявки, которая пытается хранить все сразу.

## Что пока сознательно не сделано

У этого подхода есть и осознанные ограничения.

Сейчас в delivered-срезе:

- `approved` создает только `draft`-запись;
- более богатые сценические статусы еще не открыты наружу;
- автоматическое удаление лайнапа при смене статуса назад не включено;
- история заявок для комика и более богатое редактирование для организатора остаются следующими шагами.

Это выглядит как “не до конца автоматизировано”, но на самом деле это нормальная цена за безопасный additive rollout. Сначала проект фиксирует инварианты и только потом наращивает более спорную автоматику.

## Практический вывод

Главный вывод этой истории не в том, что проекту понадобились еще две таблицы и несколько новых route-ов.

Главный вывод в другом: если продукту нужен управляемый вечер с ручной расстановкой, режимом ведущего и будущим сценическим состоянием, нельзя считать одобренную заявку и место в программе одной и той же сущностью.

В `InComedy` это выражено очень предметно:

- ревью живет отдельно;
- `approved` только открывает дверь в лайнап;
- лайнап получает собственный черновой слот;
- порядок фиксируется явно;
- дальнейшие сценические правила не прячутся в побочные эффекты ревью.

Именно такие решения обычно и определяют, будет ли следующий рост продукта управляемым или вся логика потом начнет путаться в “почти одинаковых” статусах.
