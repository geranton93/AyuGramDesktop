// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/ui/boxes/delete_my_messages_box.h"

#include "apiwrap.h"
#include "lang_auto.h"
#include "base/call_delayed.h"
#include "base/random.h"
#include "base/weak_ptr.h"
#include "base/weak_qptr.h"
#include "data/data_channel.h"
#include "data/data_session.h"
#include "main/main_session.h"
#include "mtproto/sender.h"
#include "styles/style_boxes.h"
#include "styles/style_layers.h"
#include "styles/style_widgets.h"
#include "ui/layers/generic_box.h"
#include "ui/painter.h"
#include "ui/rp_widget.h"
#include "ui/toast/toast.h"
#include "ui/vertical_list.h"
#include "ui/widgets/buttons.h"
#include "ui/widgets/labels.h"
#include "ui/wrap/vertical_layout.h"
#include "window/window_session_controller.h"

namespace AyuUi {

namespace {

constexpr auto kBatchLimit = 100;
constexpr auto kMaxCollectedMessages = 100'000;
constexpr auto kMaxSearchPages = 1'000;
constexpr auto kMaxDeleteRetries = 3;
constexpr auto kBatchDelayMin = crl::time(500);
constexpr auto kBatchDelayJitter = 500;
constexpr auto kRetryDelay = crl::time(1'000);
constexpr auto kDoneCloseDelay = crl::time(1200);

enum class Phase {
	Confirm,
	Searching,
	Deleting,
	Done,
};

struct State {
	base::weak_qptr<Ui::GenericBox> box;
	rpl::variable<Phase> phase = Phase::Confirm;
	rpl::variable<int> found = 0;
	rpl::variable<int> processed = 0;
	rpl::variable<int> deleted = 0;
	rpl::variable<int> failed = 0;
	rpl::variable<int> total = 0;
	bool limitReached = false;
};

[[nodiscard]] bool IsAlive(const State &state) {
	return state.box.get() != nullptr;
}

void ShowDeleteResult(const State &state) {
	if (!IsAlive(state)) {
		return;
	}
	const auto deleted = state.deleted.current();
	const auto total = state.total.current();
	if (deleted == total) {
		Ui::Toast::Show(tr::ayu_DeleteOwnMessagesDone(
			tr::now,
			lt_count,
			deleted));
	} else {
		Ui::Toast::Show(tr::ayu_DeleteOwnMessagesPartial(
			tr::now,
			lt_count1,
			QString::number(deleted),
			lt_count2,
			QString::number(total)));
	}
}

class ProgressBar final : public Ui::RpWidget {
public:
	explicit ProgressBar(QWidget *parent) : RpWidget(parent) {
		resize(0, st::ayuOperationProgressHeight);
	}
	void setValue(float64 value) {
		const auto clamped = std::clamp(value, 0., 1.);
		if (!qFuzzyCompare(_value, clamped)) {
			_value = clamped;
			update();
		}
	}

protected:
	void paintEvent(QPaintEvent *) override {
		auto p = QPainter(this);
		p.setRenderHint(QPainter::Antialiasing);
		const auto radius = height() / 2.;
		p.setPen(Qt::NoPen);
		p.setBrush(st::windowBgRipple);
		p.drawRoundedRect(rect(), radius, radius);
		const auto filled = int(width() * _value);
		if (filled > 0) {
			p.setBrush(st::windowBgActive);
			p.drawRoundedRect(QRect(0, 0, filled, height()), radius, radius);
		}
	}

private:
	float64 _value = 0.;
};

void RunDelete(
		not_null<Main::Session*> session,
		PeerId peerId,
		not_null<MTP::Sender*> api,
		std::shared_ptr<std::vector<MsgId>> collected,
		std::shared_ptr<State> state) {
	if (!IsAlive(*state)) {
		return;
	}
	const auto total = int(collected->size());
	state->total = total;
	state->processed = 0;
	state->deleted = 0;
	state->failed = 0;
	if (!total) {
		state->phase = Phase::Done;
		Ui::Toast::Show(tr::ayu_DeleteOwnMessagesNone(tr::now));
		return;
	}
	state->phase = Phase::Deleting;
	const auto weakSession = base::make_weak(session);

	const auto step = std::make_shared<Fn<void(int, int)>>();
	const auto weakStep = std::weak_ptr<Fn<void(int, int)>>(step);
	*step = [=](int index, int retries) {
		const auto keepAlive = weakStep.lock();
		if (!keepAlive) {
			return;
		}
		if (!IsAlive(*state)) {
			*keepAlive = nullptr;
			return;
		}
		const auto current = weakSession.get();
		if (!current) {
			state->phase = Phase::Done;
			*keepAlive = nullptr;
			return;
		}
		const auto currentPeer = current->data().peer(peerId);
		if (index >= int(collected->size())) {
			state->phase = Phase::Done;
			ShowDeleteResult(*state);
			*keepAlive = nullptr;
			return;
		}
		QVector<MTPint> ids;
		const auto take = std::min<int>(kBatchLimit, collected->size() - index);
		ids.reserve(take);
		for (auto i = 0; i < take; ++i) {
			ids.push_back(MTP_int((*collected)[index + i].bare));
		}

		const auto advance = [=](int newIndex, int newRetries = 0) {
			if (!IsAlive(*state)) {
				*keepAlive = nullptr;
				return;
			}
			state->processed = newIndex;
			const auto delay = kBatchDelayMin
				+ crl::time(base::RandomValue<int>() % kBatchDelayJitter);
			base::call_delayed(delay, [=] {
				if (IsAlive(*state)) {
					(*keepAlive)(newIndex, newRetries);
				} else {
					*keepAlive = nullptr;
				}
			});
		};
		const auto done = [=](const MTPmessages_AffectedMessages &result) {
			if (!IsAlive(*state)) {
				*keepAlive = nullptr;
				return;
			}
			const auto current = weakSession.get();
			if (!current) {
				state->phase = Phase::Done;
				*keepAlive = nullptr;
				return;
			}
			const auto currentPeer = current->data().peer(peerId);
			current->api().applyAffectedMessages(currentPeer, result);
			if (currentPeer->isChannel()) {
				current->data().processMessagesDeleted(currentPeer->id, ids);
			} else {
				current->data().processNonChannelMessagesDeleted(ids);
			}
			state->deleted = state->deleted.current() + ids.size();
			advance(index + ids.size());
		};
		const auto fail = [=](const MTP::Error &error) {
			DEBUG_LOG(("DeleteOwnMessages: batch failed: %1").arg(error.type()));
			if (!IsAlive(*state)) {
				*keepAlive = nullptr;
				return;
			}
			const auto type = error.type();
			const auto retryable = type.startsWith(u"FLOOD_WAIT_"_q)
				|| type.startsWith(u"FLOOD_PREMIUM_WAIT_"_q);
			if (retryable && retries < kMaxDeleteRetries) {
				base::call_delayed(kRetryDelay * (retries + 1), [=] {
					if (IsAlive(*state)) {
						(*keepAlive)(index, retries + 1);
					} else {
						*keepAlive = nullptr;
					}
				});
				return;
			}
			state->failed = state->failed.current() + ids.size();
			if (type == u"MESSAGE_DELETE_FORBIDDEN"_q
				|| type == u"MSG_ID_INVALID"_q
				|| type == u"MESSAGE_ID_INVALID"_q) {
				advance(index + ids.size());
				return;
			}
			state->processed = index + ids.size();
			state->phase = Phase::Done;
			ShowDeleteResult(*state);
			*keepAlive = nullptr;
		};

		if (const auto channel = currentPeer->asChannel()) {
			api->request(MTPchannels_DeleteMessages(
				channel->inputChannel(),
				MTP_vector<MTPint>(ids)))
				.done(done)
				.fail(fail)
				.handleFloodErrors()
				.send();
		} else {
			using Flag = MTPmessages_DeleteMessages::Flag;
			api->request(MTPmessages_DeleteMessages(
				MTP_flags(Flag::f_revoke),
				MTP_vector<MTPint>(ids)))
				.done(done)
				.fail(fail)
				.handleFloodErrors()
				.send();
		}
	};
	(*step)(0, 0);
}

void SearchOwn(
		not_null<Main::Session*> session,
		PeerId peerId,
		not_null<MTP::Sender*> api,
		std::shared_ptr<State> state) {
	if (!IsAlive(*state)) {
		return;
	}
	const auto weakSession = base::make_weak(session);
	const auto collected = std::make_shared<std::vector<MsgId>>();
	const auto pages = std::make_shared<int>(0);

	state->phase = Phase::Searching;
	state->found = 0;
	state->limitReached = false;

	const auto walker = std::make_shared<Fn<void(MsgId)>>();
	const auto weakWalker = std::weak_ptr<Fn<void(MsgId)>>(walker);
	*walker = [=](MsgId offsetId) {
		const auto keepAlive = weakWalker.lock();
		if (!keepAlive) {
			return;
		}
		if (!IsAlive(*state)) {
			*keepAlive = nullptr;
			return;
		}
		const auto current = weakSession.get();
		if (!current) {
			state->phase = Phase::Done;
			*keepAlive = nullptr;
			return;
		}
		const auto currentPeer = current->data().peer(peerId);
		if (collected->size() >= kMaxCollectedMessages
			|| ++(*pages) > kMaxSearchPages) {
			state->limitReached = true;
			Ui::Toast::Show(tr::ayu_DeleteOwnMessagesLimit(
				tr::now,
				lt_count1,
				QString::number(kMaxCollectedMessages)));
			*keepAlive = nullptr;
			RunDelete(current, peerId, api, collected, state);
			return;
		}
		using Flag = MTPmessages_Search::Flag;
		api->request(MTPmessages_Search(
			MTP_flags(Flag::f_from_id),
			currentPeer->input(),
			MTP_string(),
			MTP_inputPeerSelf(),
			MTPInputPeer(),
			MTPVector<MTPReaction>(),
			MTP_int(0),
			MTP_inputMessagesFilterEmpty(),
			MTP_int(0),
			MTP_int(0),
			MTP_int(offsetId.bare),
			MTP_int(0),
			MTP_int(kBatchLimit),
			MTP_int(0),
			MTP_int(0),
			MTP_long(0)))
				.done([=](const MTPmessages_Messages &result) {
					if (!IsAlive(*state)) {
						*keepAlive = nullptr;
						return;
					}
					MsgId minId;
					auto batchCount = 0;
					const auto handle = [&](const QVector<MTPMessage> &messages) {
						batchCount = int(messages.size());
						for (const auto &msg : messages) {
							msg.match([&](const MTPDmessage &data) {
								if (collected->size() >= kMaxCollectedMessages) {
									state->limitReached = true;
									return;
								}
								const auto id = MsgId(data.vid().v);
								if (!id) {
									return;
								}
								if (!minId || id < minId) {
									minId = id;
								}
								collected->push_back(id);
								state->found = state->found.current() + 1;
							}, [](const auto &) {
							});
						}
					};
					result.match(
						[&](const MTPDmessages_messages &d) {
							handle(d.vmessages().v);
						},
						[&](const MTPDmessages_messagesSlice &d) {
							handle(d.vmessages().v);
						},
						[&](const MTPDmessages_channelMessages &d) {
							handle(d.vmessages().v);
						},
						[&](const MTPDmessages_messagesNotModified &) {});
					if (state->limitReached) {
						Ui::Toast::Show(tr::ayu_DeleteOwnMessagesLimit(
							tr::now,
							lt_count1,
							QString::number(kMaxCollectedMessages)));
						*keepAlive = nullptr;
						if (const auto current = weakSession.get()) {
							RunDelete(
								current,
								peerId,
								api,
								collected,
								state);
						} else {
							state->phase = Phase::Done;
						}
					} else if (batchCount == kBatchLimit && minId) {
						(*keepAlive)(minId - MsgId(1));
					} else {
						*keepAlive = nullptr;
						if (const auto current = weakSession.get()) {
							RunDelete(
								current,
								peerId,
								api,
								collected,
								state);
						} else {
							state->phase = Phase::Done;
						}
					}
				})
				.fail([=](const MTP::Error &error) {
					DEBUG_LOG(("DeleteOwnMessages: search failed: %1").arg(error.type()));
					if (IsAlive(*state)) {
						Ui::Toast::Show(error.type());
					}
					state->phase = Phase::Done;
					*keepAlive = nullptr;
				})
			.send();
	};
	(*walker)(MsgId(0));
}

} // namespace

void FillDeleteMyMessagesBox(
		not_null<Ui::GenericBox*> box,
		not_null<PeerData*> peer,
		not_null<Window::SessionController*>) {
	box->setTitle(tr::ayu_DeleteOwnMessages());

	const auto state = std::make_shared<State>();
	state->box = base::make_weak(box.get());
	const auto session = &peer->session();
	const auto weakSession = base::make_weak(session);
	const auto peerId = peer->id;
	const auto api = box->lifetime().make_state<MTP::Sender>(
		&session->mtp());
	const auto outer = box->verticalLayout();

	const auto confirm = outer->add(object_ptr<Ui::VerticalLayout>(outer));
	confirm->add(
		object_ptr<Ui::FlatLabel>(
			confirm,
			tr::ayu_DeleteOwnMessagesConfirmation(),
			st::boxLabel),
		st::boxRowPadding + QMargins(0, st::boxLittleSkip, 0, st::boxLittleSkip));

	const auto progress = outer->add(object_ptr<Ui::VerticalLayout>(outer));
	progress->hide();

	auto searchingText = rpl::combine(
		state->phase.value(),
		state->found.value()
	) | rpl::filter([](Phase phase, int) {
		return phase == Phase::Searching;
	}) | rpl::map([](Phase, int found) {
		return tr::ayu_DeleteOwnMessagesSearching(tr::now, lt_count, found);
	});

	auto deletingText = rpl::combine(
		state->phase.value(),
		state->processed.value(),
		state->total.value()
	) | rpl::filter([](Phase phase, int, int) {
		return phase == Phase::Deleting || phase == Phase::Done;
	}) | rpl::map([](Phase, int processed, int total) {
		return QString("%1 / %2").arg(processed).arg(total);
	});

	progress->add(
		object_ptr<Ui::FlatLabel>(
			progress,
			rpl::merge(
				std::move(searchingText),
				std::move(deletingText)),
			st::boxLabel),
		st::boxRowPadding + QMargins(0, st::boxLittleSkip, 0, st::boxLittleSkip));

	const auto bar = progress->add(
		object_ptr<ProgressBar>(progress),
		st::boxRowPadding + QMargins(0, 0, 0, st::boxLittleSkip));

	rpl::combine(
		state->phase.value(),
		state->processed.value(),
		state->total.value()
	) | rpl::on_next([=](Phase phase, int processed, int total) {
		if (phase == Phase::Deleting && total > 0) {
			bar->setValue(float64(processed) / float64(total));
		} else if (phase == Phase::Done) {
			bar->setValue(1.);
		} else {
			bar->setValue(0.);
		}
	}, box->lifetime());

	const auto deleteButton = box->addButton(
		tr::lng_box_delete(),
		[=] {
			confirm->hide();
			progress->show();
			if (const auto current = weakSession.get()) {
				SearchOwn(current, peerId, api, state);
			} else {
				state->phase = Phase::Done;
			}
		},
		st::attentionBoxButton);
	box->addButton(tr::lng_cancel(), [=] {
		box->closeBox();
	});

	state->phase.value() | rpl::on_next([=](Phase phase) {
		if (deleteButton) {
			deleteButton->setVisible(phase == Phase::Confirm);
		}
	}, box->lifetime());

	state->phase.value() | rpl::filter([](Phase phase) {
		return phase == Phase::Done;
	}) | rpl::on_next([weak = base::make_weak(box.get())] {
		base::call_delayed(kDoneCloseDelay, [=] {
			if (const auto strong = weak.get()) {
				strong->closeBox();
			}
		});
	}, box->lifetime());
}

} // namespace AyuUi
