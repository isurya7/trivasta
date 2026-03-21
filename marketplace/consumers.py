import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.room_id    = self.scope['url_route']['kwargs']['room_id']
        self.room_group = f'chat_{self.room_id}'
        self.user       = self.scope['user']

        if not self.user.is_authenticated:
            await self.close()
            return

        allowed, sender_type = await self.check_access()
        if not allowed:
            await self.close()
            return

        self.sender_type = sender_type
        await self.channel_layer.group_add(self.room_group, self.channel_name)
        await self.accept()
        await self.mark_read()

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group'):
            await self.channel_layer.group_discard(self.room_group, self.channel_name)

    async def receive(self, text_data):
        data   = json.loads(text_data)
        action = data.get('action', 'message')

        if action == 'message':
            content = data.get('content', '').strip()
            if not content:
                return
            await self.handle_message(content)

        elif action == 'raise_payment':
            amount = int(data.get('amount', 0))
            note   = data.get('note', '').strip()
            if self.sender_type != 'agency' or amount <= 0:
                return
            await self.handle_raise_payment(amount, note)

        elif action == 'mark_read':
            await self.mark_read()

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event['payload']))

    async def handle_message(self, content):
        result = await self.process_message(content)

        await self.channel_layer.group_send(
            self.room_group,
            {'type': 'chat_message', 'payload': result['msg']}
        )

        for sys_msg in result.get('system_messages', []):
            await self.channel_layer.group_send(
                self.room_group,
                {'type': 'chat_message', 'payload': sys_msg}
            )

    async def handle_raise_payment(self, amount, note):
        result = await self.create_payment_request(amount, note)
        await self.channel_layer.group_send(
            self.room_group,
            {'type': 'chat_message', 'payload': result}
        )

    @database_sync_to_async
    def check_access(self):
        from .models import ChatRoom
        try:
            room      = ChatRoom.objects.select_related('user', 'agency__user').get(pk=self.room_id)
            self.room = room
            is_user   = room.user == self.user
            is_agency = hasattr(self.user, 'agency') and self.user.agency == room.agency
            if is_user:   return True, 'user'
            if is_agency: return True, 'agency'
            return False, None
        except Exception:
            return False, None

    @database_sync_to_async
    def mark_read(self):
        from .models import ChatRoom
        try:
            room = ChatRoom.objects.get(pk=self.room_id)
            if self.sender_type == 'user':
                room.messages.filter(sender_type='agency', is_read=False).update(is_read=True)
            else:
                room.messages.filter(sender_type='user', is_read=False).update(is_read=True)
        except Exception:
            pass

    @database_sync_to_async
    def process_message(self, content):
        from .models import ChatRoom, Message, Booking, AgencyWarning
        from .contact_guard import is_violation, classify_violation

        room         = ChatRoom.objects.select_related('user', 'agency', 'offer__trip').get(pk=self.room_id)
        agency       = room.agency
        sender_name  = agency.name if self.sender_type == 'agency' else room.user.username
        already_paid = Booking.objects.filter(offer=room.offer, is_paid=True).exists()
        system_messages = []

        # Contact guard — only check agency messages before payment
        if not already_paid and self.sender_type == 'agency' and is_violation(content):
            violation_type = classify_violation(content)

            deleted_msg = Message.objects.create(
                room=room,
                sender_type='agency',
                content='[Message removed: personal contact details cannot be shared before payment]',
                is_read=False,
            )

            AgencyWarning.objects.create(
                agency=agency,
                room=room,
                reason='contact_sharing' if violation_type in ('phone', 'email', 'obfuscation') else 'platform_redirect',
                flagged_content=content[:500],
            )

            warning_count   = AgencyWarning.objects.filter(agency=agency).count()
            system_messages = self._create_warning_messages(room, agency, warning_count)

            return {
                'msg': {
                    'id':                 deleted_msg.id,
                    'content':            deleted_msg.content,
                    'sender_type':        'agency',
                    'sender_name':        agency.name,
                    'time':               deleted_msg.created_at.strftime('%I:%M %p'),
                    'is_payment_request': False,
                    'is_violation':       True,
                },
                'system_messages': system_messages,
            }

        # Normal message
        msg = Message.objects.create(
            room=room,
            sender_type=self.sender_type,
            content=content,
        )

        return {
            'msg': {
                'id':                 msg.id,
                'content':            msg.content,
                'sender_type':        msg.sender_type,
                'sender_name':        sender_name,
                'time':               msg.created_at.strftime('%I:%M %p'),
                'is_payment_request': False,
                'is_violation':       False,
            },
            'system_messages': [],
        }

    @database_sync_to_async
    def create_payment_request(self, amount, note):
        from .models import ChatRoom, Message, PaymentRequest

        room = ChatRoom.objects.select_related('agency').get(pk=self.room_id)
        room.payment_requests.filter(status='pending').update(status='rejected')

        msg = Message.objects.create(
            room=room,
            sender_type='agency',
            content=f"💳 Payment Request: ₹{amount:,}\n{note}",
            is_payment_request=True,
        )

        pr = PaymentRequest.objects.create(
            room=room,
            message=msg,
            amount=amount,
            note=note,
        )

        return {
            'id':                 msg.id,
            'content':            msg.content,
            'sender_type':        'agency',
            'sender_name':        room.agency.name,
            'time':               msg.created_at.strftime('%I:%M %p'),
            'is_payment_request': True,
            'is_violation':       False,
            'payment_request': {
                'id':     pr.id,
                'amount': amount,
                'note':   note,
                'status': 'pending',
            },
        }

    def _create_warning_messages(self, room, agency, warning_count):
        from .models import Message

        WARNING_TEMPLATES = {
            1: (
                "⚠️ **Trivasta Warning (1/3) — {agency}**\n\n"
                "An attempt to share personal contact details was detected and blocked.\n\n"
                "📋 **Policy Reminder:** Sharing phone numbers, emails, WhatsApp links or any "
                "external contact before payment is strictly prohibited.\n\n"
                "You have **2 warnings remaining** before your plan is downgraded."
            ),
            2: (
                "⚠️ **Trivasta Warning (2/3) — {agency}**\n\n"
                "A second violation has been detected. This is your final warning.\n\n"
                "🚨 **One more violation will result in an immediate downgrade to Starter plan.**"
            ),
            3: (
                "🚫 **Trivasta Action Taken — {agency}**\n\n"
                "Three violations recorded. **Your plan has been downgraded to Starter.**\n\n"
                "❌ You have lost access to Professional/Enterprise features.\n"
                "📞 To appeal, contact Trivasta support."
            ),
        }

        key  = min(warning_count, 3)
        text = WARNING_TEMPLATES[key].format(agency=agency.name)

        if warning_count >= 3:
            agency.plan = 'starter'
            agency.save(update_fields=['plan'])

        warn_msg = Message.objects.create(
            room=room, sender_type='agency', content=text
        )

        notice_msg = Message.objects.create(
            room=room,
            sender_type='user',
            content=(
                f"🔒 **Trivasta Notice:** A message from {agency.name} was removed "
                f"because it contained contact details. These will be shared automatically "
                f"after payment is confirmed."
            ),
        )

        return [
            {
                'id':                 warn_msg.id,
                'content':            warn_msg.content,
                'sender_type':        'agency',
                'sender_name':        agency.name,
                'time':               warn_msg.created_at.strftime('%I:%M %p'),
                'is_payment_request': False,
                'is_violation':       False,
                'is_system':          True,
            },
            {
                'id':                 notice_msg.id,
                'content':            notice_msg.content,
                'sender_type':        'user',
                'sender_name':        'Trivasta',
                'time':               notice_msg.created_at.strftime('%I:%M %p'),
                'is_payment_request': False,
                'is_violation':       False,
                'is_system':          True,
            },
        ]