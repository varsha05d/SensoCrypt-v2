package com.sensocrypt.push

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.sensocrypt.MainActivity
import com.sensocrypt.R
import com.sensocrypt.identity.UserSession
import com.sensocrypt.net.CallsApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

private const val INCOMING_CALL_CHANNEL_ID = "incoming_calls"
const val EXTRA_INCOMING_CALL_ID = "incoming_call_id"
const val EXTRA_INCOMING_CALLER_NAME = "incoming_caller_name"

/** Receives incoming-call pushes (see backend/app/api/calls.py's place_call) whether the
 * app is foregrounded, backgrounded, or not running at all -- this is what makes
 * "call by phone number" actually ring the other person, unlike v1's model where both
 * sides had to already be sitting on the manual code-entry screen. */
class SensoCryptMessagingService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        Log.i("SensoCrypt", "FCM: onNewToken (len=${token.length})")
        val session = UserSession(applicationContext)
        val authToken = session.authToken ?: run {
            Log.i("SensoCrypt", "FCM: onNewToken, not logged in yet -- will register on next login instead")
            return
        }
        CoroutineScope(Dispatchers.IO).launch {
            try {
                CallsApi().setFcmToken(token, authToken)
                Log.i("SensoCrypt", "FCM: onNewToken, registered with backend OK")
            } catch (e: Exception) {
                // Best-effort: a stale FCM token just means this device won't ring for the
                // next call until the app is opened again (which re-registers on launch).
                Log.w("SensoCrypt", "FCM: onNewToken, registration failed: ${e.message}", e)
            }
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        Log.i("SensoCrypt", "FCM: onMessageReceived, data=${message.data}, from=${message.from}")
        if (message.data["type"] != "incoming_call") {
            Log.w("SensoCrypt", "FCM: onMessageReceived, ignoring -- type != incoming_call")
            return
        }
        val callId = message.data["call_id"] ?: run {
            Log.w("SensoCrypt", "FCM: onMessageReceived, ignoring -- missing call_id")
            return
        }
        val callerName = message.data["caller_name"] ?: "Unknown"
        Log.i("SensoCrypt", "FCM: showing incoming call notification for callId=$callId")
        showIncomingCallNotification(callId, callerName)
    }

    private fun showIncomingCallNotification(callId: String, callerName: String) {
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                INCOMING_CALL_CHANNEL_ID,
                "Incoming calls",
                NotificationManager.IMPORTANCE_HIGH,
            )
            notificationManager.createNotificationChannel(channel)
        }

        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(EXTRA_INCOMING_CALL_ID, callId)
            putExtra(EXTRA_INCOMING_CALLER_NAME, callerName)
        }
        val pendingIntent = PendingIntent.getActivity(
            this,
            callId.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notification = NotificationCompat.Builder(this, INCOMING_CALL_CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher_foreground)
            .setContentTitle("Incoming call")
            .setContentText("$callerName is calling")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            // Rings/shows over the lock screen even when the app isn't open -- the whole
            // point of using push here instead of v1's "both sides already in the app" model.
            .setFullScreenIntent(pendingIntent, true)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build()

        notificationManager.notify(callId.hashCode(), notification)
    }
}
