---
article_id: null
status: "draft"
created_at: "2026-03-23T09:05:00+00:00"
updated_at: "2026-03-23T09:05:00+00:00"
scheduled_publish_at: null
moderation_comment: null
title: "Почему в InComedy сначала собран билетный контур, а уже потом будет выбран платежный шлюз"
slug: "ticketing-before-provider-choice"
cover_path: "publications/InComedy/post-assets/ticketing_before_provider_cover.png"
payload_path: ".codex-local/payloads/ticketing-before-provider-choice.json"
source_refs:
  - "/Users/abetirov/AndroidStudioProjects/InComedy/docs/context/governance/decisions-log/decisions-log-part-05.md"
  - "/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/ticketing/EventTicketingService.kt"
  - "/Users/abetirov/AndroidStudioProjects/InComedy/domain/ticketing/src/commonMain/kotlin/com/bam/incomedy/domain/ticketing/TicketOrderModels.kt"
attached_links:
  - "https://yookassa.ru/developers/payment-acceptance/getting-started/merchant-profile"
  - "https://yookassa.ru/developers/payment-acceptance/getting-started/payment-process"
  - "https://yookassa.ru/developers/using-api/webhooks"
last_synced_at: null
publish_strategy: null
last_error: null
---
# Почему в InComedy сначала собран билетный контур, а уже потом будет выбран платежный шлюз

Когда делаешь продажу билетов, очень легко начать не с билета, а с оплаты. Кажется логичным: сначала подключить провайдера, получить `checkout`, а потом уже достраивать все остальное.

В `InComedy` сознательно пошли в обратную сторону. Сначала в проекте был собран внутренний билетный контур, а уже потом в кодовой базе появился внешний PSP-кандидат. Это не случайность и не “временная недоделка”, а зафиксированное решение: [D-066](/Users/abetirov/AndroidStudioProjects/InComedy/docs/context/governance/decisions-log/decisions-log-part-05.md#L11) прямо говорит, что выбор конкретного платежного провайдера отложен до финального предрелизного этапа, а текущий `P0` должен идти через внутренний заказ, выпуск билета, QR и check-in. Та же логика зафиксирована в [backlog.md](/Users/abetirov/AndroidStudioProjects/InComedy/docs/context/product/backlog.md#L10) и в текущем состоянии архитектуры: [architecture-overview.md](/Users/abetirov/AndroidStudioProjects/InComedy/docs/context/engineering/architecture-overview.md#L55).

На практике это означает простую вещь: проект не строит билет вокруг YooKassa, CloudPayments или любого другого PSP. Проект строит билет вокруг собственных инвариантов.

## Что в проекте считается настоящим билетным контуром

Внутри `InComedy` билетный путь начинается не с платежной формы, а с модели события и его зала.

Сначала событие получает замороженный `snapshot` зала и локальные переопределения. Из этого состояния компилируется продаваемый инвентарь:

- места в рядах;
- слоты в стоячих зонах;
- места за столами.

Именно это делает [TicketingInventoryCompiler](/Users/abetirov/AndroidStudioProjects/InComedy/domain/ticketing/src/commonMain/kotlin/com/bam/incomedy/domain/ticketing/TicketingInventoryCompiler.kt#L15). Важная деталь здесь в том, что компилятор живет в доменном слое, а не внутри конкретного backend-route или UI. Это значит, что одно и то же правило сборки используется как сервером, так и будущими клиентскими сценариями preview или checkout.

Дальше [EventTicketingService.listInventoryForEvent(...)](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/ticketing/EventTicketingService.kt#L80) не пересобирает инвентарь на каждый запрос. Он синхронизирует derived inventory только если изменился `updated_at` у события. Это важный инженерный шаг: в проекте ticketing уже не живет как “сгенерируем список мест на лету и как-нибудь разберемся”, а получает собственное состояние, ревизии и TTL-логику.

Именно поэтому в архитектурном обзоре ticketing уже описан как отдельный bounded context, а не как хвост у event management: [architecture-overview.md](/Users/abetirov/AndroidStudioProjects/InComedy/docs/context/engineering/architecture-overview.md#L12).

## Почему заказ должен появиться раньше оплаты

Следующий ключевой шаг в проекте — это не платеж, а внутренний заказ.

В `InComedy` есть отдельная модель [TicketOrder](/Users/abetirov/AndroidStudioProjects/InComedy/domain/ticketing/src/commonMain/kotlin/com/bam/incomedy/domain/ticketing/TicketOrderModels.kt#L3), которая собирается из уже активных `hold`-ов. Это и есть точка, где система фиксирует:

- какие именно inventory unit выбраны;
- какая у них цена;
- в какой валюте собирается заказ;
- сколько времени заказ может жить до истечения.

Создание такого заказа идет через [createTicketOrder(...)](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/ticketing/EventTicketingService.kt#L145), а не через прямой вызов PSP. Поэтому проверка принадлежности `hold`, конфликтов, истечения времени и смешения валют происходит до того, как пользователь вообще уйдет во внешний `checkout`.

Это дает несколько практических плюсов.

Во-первых, место уже живет по правилам продукта, а не по правилам платежного кабинета. Во-вторых, если пользователь ушел на оплату и не вернулся, система умеет сама истечь и освободить блокировку. В-третьих, внешний провайдер перестает быть владельцем бизнес-смысла заказа.

Только после этого появляется [TicketCheckoutSession](/Users/abetirov/AndroidStudioProjects/InComedy/domain/ticketing/src/commonMain/kotlin/com/bam/incomedy/domain/ticketing/TicketCheckoutModels.kt#L3) и вызов [startTicketCheckout(...)](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/ticketing/EventTicketingService.kt#L227). Это уже переход во внешний мир, а не рождение самого заказа.

Для билетного контура это очень важное различие. Если перепутать эти два уровня, проект почти неизбежно начинает принимать идентификатор платежа провайдера за главный бизнес-идентификатор и привязывает весь жизненный цикл места к одному интеграционному адаптеру.

## Что появляется только после `paid`

После подтвержденной оплаты в проекте уже не “какой-то успешный callback”, а выданный билет.

Эту часть описывает [IssuedTicket](/Users/abetirov/AndroidStudioProjects/InComedy/domain/ticketing/src/commonMain/kotlin/com/bam/incomedy/domain/ticketing/IssuedTicketModels.kt#L3) и результат check-in [TicketCheckInResult](/Users/abetirov/AndroidStudioProjects/InComedy/domain/ticketing/src/commonMain/kotlin/com/bam/incomedy/domain/ticketing/IssuedTicketModels.kt#L54). Здесь особенно важны две вещи:

- билет получает собственный `qrPayload`;
- повторное сканирование не превращается в хаос, а возвращает явный результат `duplicate`.

На уровне API это разведено очень четко:

- покупатель получает свои билеты через [GET `/api/v1/me/tickets`](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/ticketing/TicketingRoutes.kt#L329);
- персонал гасит билет через [POST `/api/v1/checkin/scan`](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/ticketing/TicketingRoutes.kt#L380).

То есть check-in в проекте — это не “второй маленький модуль потом как-нибудь”. Он сразу проектируется как продолжение того же order/ticket lifecycle.

Дальше этот серверный контракт уже вынесен в shared/mobile слой:

- общий MVI-координатор: [TicketingViewModel](/Users/abetirov/AndroidStudioProjects/InComedy/feature/ticketing/src/commonMain/kotlin/com/bam/incomedy/feature/ticketing/TicketingViewModel.kt#L21);
- iOS bridge для snapshot-моделей: [TicketingBridge](/Users/abetirov/AndroidStudioProjects/InComedy/shared/src/commonMain/kotlin/com/bam/incomedy/shared/ticketing/TicketingBridge.kt#L10);
- Android-вкладка кошелька и staff scan: [TicketWalletTab.kt](/Users/abetirov/AndroidStudioProjects/InComedy/composeApp/src/main/kotlin/com/bam/incomedy/feature/ticketing/ui/TicketWalletTab.kt#L62);
- iOS-экран кошелька и проверки: [TicketWalletView.swift](/Users/abetirov/AndroidStudioProjects/InComedy/iosApp/iosApp/Features/Ticketing/UI/TicketWalletView.swift#L6).

Это еще один сильный признак правильной архитектуры: мобильные приложения уже показывают билет и умеют проверять QR, хотя продукт все еще не зафиксировал окончательный PSP.

## Где брать параметры YooKassa и как они должны лечь в проект

Хотя конкретный провайдер пока не выбран, в репозитории уже есть отключенный кандидат — `YooKassa`. И как раз здесь особенно важно не путать “код есть” и “продукт выбрал провайдера”.

Официальная схема у YooKassa такая:

1. `shopId` берется из настроек магазина в личном кабинете YooKassa.
2. `secret key` выпускается отдельно в разделе API-ключей.
3. Серверные запросы к API подписываются через `Basic Auth` как `shopId:secretKey`.
4. При создании платежа нужно передать `return_url`, который контролирует сам продукт.
5. Вебхуку нельзя доверять в сыром виде: у него нужно проверять источник и перепроверять текущее состояние платежа через API провайдера.

Официальные источники:

- [Как получить `shopId` и настроить магазин](https://yookassa.ru/developers/payment-acceptance/getting-started/merchant-profile)
- [Создание платежа и `return_url`](https://yookassa.ru/developers/payment-acceptance/getting-started/payment-process)
- [Входящие уведомления и правила работы с webhook](https://yookassa.ru/developers/using-api/webhooks)

В `InComedy` эти параметры уже разложены по server-side конфигу:

- описание параметров и их смысла: [YooKassaConfig](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/config/AppConfig.kt#L112);
- загрузка только при `YOOKASSA_ENABLED=true`: [yooKassaConfig()](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/config/AppConfig.kt#L216);
- пример переменных окружения: [server/.env.example](/Users/abetirov/AndroidStudioProjects/InComedy/server/.env.example) и [deploy/server/.env.example](/Users/abetirov/AndroidStudioProjects/InComedy/deploy/server/.env.example).

Ключевые переменные здесь такие:

- `YOOKASSA_ENABLED`
- `YOOKASSA_SHOP_ID`
- `YOOKASSA_SECRET_KEY`
- `YOOKASSA_RETURN_URL`
- `YOOKASSA_API_BASE_URL`
- `YOOKASSA_CAPTURE`

Использоваться они должны только на сервере. Не в мобильном приложении, не в git, не в `shared`, не в Android/iOS runtime-конфиге. Это видно и по реализации [YooKassaCheckoutGateway](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/payments/yookassa/YooKassaCheckoutGateway.kt#L24): адаптер сам строит `Basic Auth`, сам добавляет `return_url`, сам пишет `order_id` и `event_id` в metadata и сам же потом читает статус платежа обратно из API.

Отдельно важно, что webhook-путь в проекте не просто принимает уведомление и верит ему. В [TicketingRoutes](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/ticketing/TicketingRoutes.kt#L169) идет:

- peer-based rate limit;
- проверка source IP;
- проверка допустимых event types;
- повторное чтение актуального статуса платежа из провайдера;
- только потом идемпотентный переход order/session/inventory.

Список допущенных сетей и правило доверия `X-Forwarded-For` только при локальном reverse proxy вынесены отдельно в [YooKassaWebhookSecurity.kt](/Users/abetirov/AndroidStudioProjects/InComedy/server/src/main/kotlin/com/bam/incomedy/server/payments/yookassa/YooKassaWebhookSecurity.kt#L7).

## Какие тут самые опасные ошибки

Если смотреть на этот путь практично, рисков здесь четыре.

Первый риск — организационный. Наличие готового адаптера в репозитории очень легко спутать с фактом принятого продуктового решения. Именно поэтому рядом с `YooKassa` в проекте появилось отдельное правило [D-065](/Users/abetirov/AndroidStudioProjects/InComedy/docs/context/governance/decisions-log/decisions-log-part-05.md#L3): код, черновик документации и пример env не считаются подтверждением выбора провайдера.

Второй риск — утечка секрета. `shopId` можно считать техническим идентификатором, но `secret key` — это уже полноценный server credential. Его нельзя хранить в мобильном клиенте, коммитить в репозиторий или передавать через слабые локальные скрипты без контроля.

Третий риск — доверие к вебхуку как к источнику истины. Если просто принять входящий payload и сразу отметить заказ как `paid`, можно получить ложные продажи, ошибочный выпуск билетов и конфликт с реальным состоянием платежа.

Четвертый риск — архитектурный lock-in. Если начать проектировать inventory, order и ticket не как продуктовые сущности, а как отражение полей конкретного провайдера, любая смена PSP потом превращается почти в переписывание домена.

## Практический вывод

Главная мысль этой истории для меня очень простая: хороший билетный контур начинается не с кнопки оплаты.

Он начинается с того, что у тебя есть:

- неизменяемый snapshot зала;
- дискретный продаваемый инвентарь;
- hold с TTL;
- внутренний заказ, который живет по правилам продукта;
- идемпотентный выпуск билета;
- явная семантика check-in и duplicate scan.

Когда это уже собрано, платежный шлюз становится адаптером. Важным, но все же адаптером.

Именно поэтому в `InComedy` сначала были доведены `order -> ticket -> QR -> check-in`, а окончательный PSP оставлен на потом. Это не задержка выбора. Это способ не строить весь ticketing вокруг чужого кабинета.
