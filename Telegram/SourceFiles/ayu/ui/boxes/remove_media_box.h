// This is the source code of AyuGram for Desktop.
//
// We do not and cannot prevent the use of our code,
// but be respectful and credit the original author.
//
// Copyright @Radolyn, 2026
#pragma once

class PeerData;

namespace Data {
class ForumTopic;
} // namespace Data

namespace Window {
class SessionController;
} // namespace Window

namespace Ui {
class GenericBox;
} // namespace Ui

namespace AyuUi {

void FillRemoveMediaBox(
	not_null<Ui::GenericBox*> box,
	not_null<PeerData*> peer,
	not_null<Window::SessionController*> controller,
	Data::ForumTopic *topic);

} // namespace AyuUi
