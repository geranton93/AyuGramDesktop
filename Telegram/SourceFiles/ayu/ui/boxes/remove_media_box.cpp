// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#include "ayu/ui/boxes/remove_media_box.h"

#include "apiwrap.h"
#include "lang_auto.h"
#include "base/call_delayed.h"
#include "base/random.h"
#include "base/weak_ptr.h"
#include "base/weak_qptr.h"
#include "data/data_channel.h"
#include "data/data_session.h"
#include "data/data_user.h"
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
#include "ui/widgets/checkbox.h"
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
	Selecting,
	Searching,
	Deleting,
	Done,
};

struct State {
	base::weak_qptr<Ui::GenericBox> box;
	rpl::variable<Phase> phase = Phase::Selecting;
	rpl::variable<int> found = 0;
	rpl::variable<int> processed = 0;
	rpl::variable<int> deleted = 0;
	rpl::variable<int> failed = 0;
	rpl::variable<int> total = 0;
	bool limitReached = false;
	QString searchError;
};

[[nodiscard]] bool IsAlive(const State &state) {
	return state.box.get() != nullptr;
}

void ShowRemoveResult(const State &state) {
	if (!IsAlive(state)) {
		return;
	}
	const auto deleted = state.deleted.current();
	const auto total = state.total.current();
	if (deleted == total) {
		Ui::Toast::Show(tr::ayu_RemoveMediaDone(
			tr::now,
			lt_count,
			deleted));
	} else {
		Ui::Toast::Show(tr::ayu_RemoveMediaPartial(
			tr::now,
			lt_count1,
			QString::number(deleted),
			lt_count2,
			QString::number(total)));
	}
}

struct Selector {
	MTPMessagesFilter filter;
	bool stickersOnly = false;
};

bool MessageIsSticker(const MTPDmessage &data) {
	const auto media = data.vmedia();
	if (!media) {
		return false;
	}
	auto isSticker = false;
	media->match([&](const MTPDmessageMediaDocument &d) {
		if (const auto doc = d.vdocument()) {
			doc->match([&](const MTPDdocument &dd) {
				for (const auto &attr : dd.vattributes().v) {
					if (attr.type() == mtpc_documentAttributeSticker) {
						isSticker = true;
						return;
					}
				}
			}, [](const MTPDdocumentEmpty &) {
			});
		}
	}, [](const auto &) {
	});
	return isSticker;
}

void WalkMessageIds(
		const MTPmessages_Messages &response,
		bool stickersOnly,
		bool deleteMine,
		bool deleteTheirs,
		Fn<void(MsgId)> consume) {
	const auto handle = [&](const QVector<MTPMessage> &messages) {
		for (const auto &msg : messages) {
			msg.match([&](const MTPDmessage &data) {
				if (stickersOnly && !MessageIsSticker(data)) {
					return;
				}
				// Scope by sender. The batch is already type-filtered, so if
				// no sender sweep is on, keep everything (all senders).
				// Otherwise keep only the matching sender's messages, using
				// the `out` flag — reliable in 1-on-1 chats, where
				// messages.search ignores from_id. out == true: we sent it;
				// out == false: the other side did.
				const auto out = data.is_out();
				const auto keep = (!deleteMine && !deleteTheirs)
					|| (deleteMine && out)
					|| (deleteTheirs && !out);
				if (!keep) {
					return;
				}
				consume(MsgId(data.vid().v));
			}, [](const auto &) {
			});
		}
	};
	response.match(
		[&](const MTPDmessages_messages &d) { handle(d.vmessages().v); },
		[&](const MTPDmessages_messagesSlice &d) { handle(d.vmessages().v); },
		[&](const MTPDmessages_channelMessages &d) { handle(d.vmessages().v); },
		[&](const MTPDmessages_messagesNotModified &) {});
}

int RawMessagesCount(const MTPmessages_Messages &response) {
	auto count = 0;
	response.match(
		[&](const MTPDmessages_messages &d) { count = d.vmessages().v.size(); },
		[&](const MTPDmessages_messagesSlice &d) { count = d.vmessages().v.size(); },
		[&](const MTPDmessages_channelMessages &d) { count = d.vmessages().v.size(); },
		[&](const MTPDmessages_messagesNotModified &) {});
	return count;
}

MsgId MinMessageId(const MTPmessages_Messages &response) {
	MsgId result;
	const auto handle = [&](const QVector<MTPMessage> &messages) {
		for (const auto &msg : messages) {
			msg.match([&](const MTPDmessage &data) {
				const auto id = MsgId(data.vid().v);
				if (id && (!result || id < result)) {
					result = id;
				}
			}, [](const auto &) {
			});
		}
	};
	response.match(
		[&](const MTPDmessages_messages &d) { handle(d.vmessages().v); },
		[&](const MTPDmessages_messagesSlice &d) { handle(d.vmessages().v); },
		[&](const MTPDmessages_channelMessages &d) { handle(d.vmessages().v); },
		[&](const MTPDmessages_messagesNotModified &) {});
	return result;
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
		std::shared_ptr<base::flat_set<MsgId>> collected,
		bool revoke,
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
		Ui::Toast::Show(tr::ayu_RemoveMediaNoneFound(tr::now));
		return;
	}
	state->phase = Phase::Deleting;
	const auto ordered = std::make_shared<std::vector<MsgId>>(
		collected->begin(),
		collected->end());
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
		if (index >= int(ordered->size())) {
			state->phase = Phase::Done;
			ShowRemoveResult(*state);
			*keepAlive = nullptr;
			return;
		}
		QVector<MTPint> ids;
		const auto take = std::min<int>(kBatchLimit, ordered->size() - index);
		ids.reserve(take);
		for (auto i = 0; i < take; ++i) {
			ids.push_back(MTP_int((*ordered)[index + i].bare));
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
			DEBUG_LOG(("RemoveMedia: delete batch failed: %1").arg(error.type()));
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
			ShowRemoveResult(*state);
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
				MTP_flags(revoke ? Flag::f_revoke : Flag(0)),
				MTP_vector<MTPint>(ids)))
				.done(done)
				.fail(fail)
				.handleFloodErrors()
				.send();
		}
	};
	(*step)(0, 0);
}

void SearchAndDelete(
		not_null<Main::Session*> session,
		PeerId peerId,
		not_null<MTP::Sender*> api,
		std::vector<Selector> selectors,
		bool deleteMine,
		bool deleteTheirs,
		bool revoke,
		std::shared_ptr<State> state) {
	if (!IsAlive(*state)) {
		return;
	}
	const auto weakSession = base::make_weak(session);
	const auto collected = std::make_shared<base::flat_set<MsgId>>();
	const auto remaining = std::make_shared<int>(int(selectors.size()));
	const auto stopSearch = std::make_shared<bool>(false);
	const auto searchFailed = std::make_shared<bool>(false);
	const auto searchError = std::make_shared<QString>();

	state->phase = Phase::Searching;
	state->found = 0;
	state->limitReached = false;
	state->searchError.clear();

	if (selectors.empty()) {
		state->phase = Phase::Done;
		Ui::Toast::Show(tr::ayu_RemoveMediaNoneFound(tr::now));
		return;
	}

	const auto finishOne = [=] {
		if (*remaining <= 0) {
			return;
		}
		if (--(*remaining) == 0) {
			if (!IsAlive(*state)) {
				return;
			}
			if (*searchFailed) {
				state->phase = Phase::Done;
				Ui::Toast::Show(*searchError);
			} else {
				if (const auto current = weakSession.get()) {
					RunDelete(
						current,
						peerId,
						api,
						collected,
						revoke,
						state);
				} else {
					state->phase = Phase::Done;
				}
			}
		}
	};

	for (const auto &selector : selectors) {
		const auto filter = selector.filter;
		const auto stickersOnly = selector.stickersOnly;
		const auto pages = std::make_shared<int>(0);
		const auto walker = std::make_shared<Fn<void(MsgId)>>();
		const auto weakWalker = std::weak_ptr<Fn<void(MsgId)>>(walker);
		*walker = [=](MsgId offsetId) {
			const auto keepAlive = weakWalker.lock();
			if (!keepAlive) {
				return;
			}
			if (!IsAlive(*state)) {
				*keepAlive = nullptr;
				finishOne();
				return;
			}
			const auto current = weakSession.get();
			if (!current) {
				*keepAlive = nullptr;
				finishOne();
				return;
			}
			const auto currentPeer = current->data().peer(peerId);
			if (*stopSearch) {
				*keepAlive = nullptr;
				finishOne();
				return;
			}
			if (collected->size() >= kMaxCollectedMessages
				|| ++(*pages) > kMaxSearchPages) {
				*stopSearch = true;
				state->limitReached = true;
				Ui::Toast::Show(tr::ayu_RemoveMediaLimit(
					tr::now,
					lt_count1,
					QString::number(kMaxCollectedMessages)));
				*keepAlive = nullptr;
				finishOne();
				return;
			}
			api->request(MTPmessages_Search(
				MTP_flags(0),
				currentPeer->input(),
				MTP_string(),
				MTPInputPeer(),
				MTPInputPeer(),
				MTPVector<MTPReaction>(),
				MTP_int(0),
				filter,
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
						finishOne();
						return;
					}
					if (*stopSearch) {
						*keepAlive = nullptr;
						finishOne();
						return;
					}
					auto reachedLimit = false;
					WalkMessageIds(
							result,
							stickersOnly,
							deleteMine,
							deleteTheirs,
							[&](MsgId id) {
						if (!id) {
							return;
						}
						if (collected->size() >= kMaxCollectedMessages) {
							reachedLimit = true;
							return;
						}
						if (collected->insert(id).second) {
							state->found = state->found.current() + 1;
						}
					});
					if (reachedLimit) {
						*stopSearch = true;
						state->limitReached = true;
						Ui::Toast::Show(tr::ayu_RemoveMediaLimit(
							tr::now,
							lt_count1,
							QString::number(kMaxCollectedMessages)));
						*keepAlive = nullptr;
						finishOne();
						return;
					}
					const auto rawCount = RawMessagesCount(result);
					const auto minId = MinMessageId(result);
					if (rawCount == kBatchLimit && minId) {
						(*keepAlive)(minId - MsgId(1));
					} else {
						*keepAlive = nullptr;
						finishOne();
					}
				})
				.fail([=](const MTP::Error &error) {
					DEBUG_LOG(("RemoveMedia: search failed: %1").arg(error.type()));
					*searchFailed = true;
					*stopSearch = true;
					*searchError = error.type();
					state->searchError = error.type();
					*keepAlive = nullptr;
					finishOne();
				})
				.send();
		};
		(*walker)(MsgId(0));
	}
}

} // namespace

void FillRemoveMediaBox(
		not_null<Ui::GenericBox*> box,
		not_null<PeerData*> peer,
		not_null<Window::SessionController*>) {
	box->setTitle(tr::ayu_RemoveMediaTitle());

	const auto state = std::make_shared<State>();
	state->box = base::make_weak(box.get());
	const auto session = &peer->session();
	const auto weakSession = base::make_weak(session);
	const auto peerId = peer->id;
	const auto api = box->lifetime().make_state<MTP::Sender>(
		&session->mtp());

	struct Entry {
		Ui::Checkbox *checkbox = nullptr;
		MTPMessagesFilter filter;
		bool stickersOnly = false;
	};
	const auto entries = box->lifetime().make_state<std::vector<Entry>>();

	const auto outer = box->verticalLayout();
	const auto selection = outer->add(object_ptr<Ui::VerticalLayout>(outer));

	const auto addType = [&](
			MTPMessagesFilter filter,
			bool stickersOnly,
			rpl::producer<QString> label) {
		const auto checkbox = selection->add(
			object_ptr<Ui::Checkbox>(
				selection,
				std::move(label),
				false,
				st::defaultBoxCheckbox),
			st::boxRowPadding + QMargins(0, st::boxLittleSkip / 2, 0, st::boxLittleSkip / 2));
		entries->push_back({ checkbox, filter, stickersOnly });
	};

	addType(MTP_inputMessagesFilterPhotos(), false, tr::ayu_RemoveMediaPhotos());
	addType(MTP_inputMessagesFilterVideo(), false, tr::ayu_RemoveMediaVideos());
	addType(MTP_inputMessagesFilterVoice(), false, tr::ayu_RemoveMediaVoice());
	addType(MTP_inputMessagesFilterRoundVideo(), false, tr::ayu_RemoveMediaVideoMessages());
	addType(MTP_inputMessagesFilterGif(), false, tr::ayu_RemoveMediaGifs());

	// Telegram's API exposes no inputMessagesFilterStickers, and the
	// server classifies stickers separately from files, so inputMessagesFilterDocument
	// returns zero stickers. The only correct path is to scan the entire
	// chat history with the empty filter and pick out documents whose
	// attributes include documentAttributeSticker on the client side.
	addType(MTP_inputMessagesFilterEmpty(), true, tr::ayu_RemoveMediaStickers());

	const auto user = peer->asUser();
	const auto isSelf = user && user->isSelf();
	const auto isBasicChat = peer->isChat();
	const auto showRevokeToggle = (user && !isSelf) || isBasicChat;

	// In a 1-on-1 user chat (not Saved Messages) there is a single "other
	// side", so we can offer two extra sweeps scoped by sender: every media
	// type the current user sent, or every media type the other user sent.
	const auto isPrivateChat = user && !isSelf;
	Ui::Checkbox *myMedia = nullptr;
	Ui::Checkbox *theirMedia = nullptr;
	if (isPrivateChat) {
		Ui::AddSkip(selection, st::boxLittleSkip);
		Ui::AddDivider(selection);
		Ui::AddSkip(selection, st::boxLittleSkip);

		const auto pad = st::boxRowPadding
			+ QMargins(0, st::boxLittleSkip / 2, 0, st::boxLittleSkip / 2);
		myMedia = selection->add(
			object_ptr<Ui::Checkbox>(
				selection,
				tr::ayu_RemoveMediaMine(),
				false,
				st::defaultBoxCheckbox),
			pad);
		theirMedia = selection->add(
			object_ptr<Ui::Checkbox>(
				selection,
				tr::ayu_RemoveMediaTheirs(
					lt_user,
					rpl::single(peer->shortName())),
				false,
				st::defaultBoxCheckbox),
			pad);
	}

	Ui::Checkbox *revoke = nullptr;
	if (showRevokeToggle) {
		Ui::AddSkip(selection, st::boxLittleSkip);
		Ui::AddDivider(selection);
		Ui::AddSkip(selection, st::boxLittleSkip);

		auto revokeLabel = isBasicChat
			? tr::ayu_RemoveMediaRevokeGroup()
			: tr::ayu_RemoveMediaRevoke(lt_user, rpl::single(peer->shortName()));
		revoke = selection->add(
			object_ptr<Ui::Checkbox>(
				selection,
				std::move(revokeLabel),
				false,
				st::defaultBoxCheckbox),
			st::boxRowPadding + QMargins(0, st::boxLittleSkip / 2, 0, st::boxLittleSkip / 2));
	}

	const auto progress = outer->add(object_ptr<Ui::VerticalLayout>(outer));
	progress->hide();

	auto searchingText = rpl::combine(
		state->phase.value(),
		state->found.value()
	) | rpl::filter([](Phase phase, int) {
		return phase == Phase::Searching;
	}) | rpl::map([](Phase, int found) {
		return tr::ayu_RemoveMediaSearching(tr::now, lt_count, found);
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

	const auto removeButton = box->addButton(
		tr::ayu_RemoveMediaButton(),
		[=] {
			const auto deleteMine = myMedia && myMedia->checked();
			const auto deleteTheirs = theirMedia && theirMedia->checked();
			const auto senderScoped = deleteMine || deleteTheirs;

			auto anyType = false;
			for (const auto &entry : *entries) {
				if (entry.checkbox->checked()) {
					anyType = true;
					break;
				}
			}

			// Types to act on come from the ticked type boxes. If none are
			// ticked but a sender sweep is, act on every media type. The
			// sender sweep (if any) then narrows each searched type to the
			// matching sender, client-side in WalkMessageIds.
			std::vector<Selector> selected;
			for (const auto &entry : *entries) {
				if (entry.checkbox->checked() || (!anyType && senderScoped)) {
					selected.push_back({ entry.filter, entry.stickersOnly });
				}
			}
			if (selected.empty()) {
				Ui::Toast::Show(tr::ayu_RemoveMediaNothingSelected(tr::now));
				return;
			}
			const auto revokeChecked = revoke
				? revoke->checked()
				: !isSelf;
			selection->hide();
			progress->show();
			if (const auto current = weakSession.get()) {
				SearchAndDelete(
					current,
					peerId,
					api,
					std::move(selected),
					deleteMine,
					deleteTheirs,
					revokeChecked,
					state);
			} else {
				state->phase = Phase::Done;
			}
		},
		st::attentionBoxButton);
	box->addButton(tr::lng_cancel(), [=] {
		box->closeBox();
	});

	state->phase.value() | rpl::on_next([=](Phase phase) {
		const auto selecting = (phase == Phase::Selecting);
		if (removeButton) {
			removeButton->setVisible(selecting);
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
