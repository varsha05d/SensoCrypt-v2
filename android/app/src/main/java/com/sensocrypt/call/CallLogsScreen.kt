package com.sensocrypt.call

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CallMade
import androidx.compose.material.icons.filled.CallReceived
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.HourglassTop
import androidx.compose.material.icons.filled.PersonOff
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sensocrypt.identity.UserSession
import com.sensocrypt.net.CallLogEntry
import com.sensocrypt.net.CallsApi
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@Composable
fun CallLogsScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val userSession = remember { UserSession(context) }
    val callsApi = remember { CallsApi() }

    var logs by remember { mutableStateOf<List<CallLogEntry>?>(null) }
    var errorText by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        val authToken = userSession.authToken
        if (authToken == null) {
            errorText = "Not logged in"
            return@LaunchedEffect
        }
        try {
            logs = callsApi.callLogs(authToken)
        } catch (e: Exception) {
            errorText = "Couldn't load call logs: ${e.message}"
        }
    }

    Box(modifier = Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Column(modifier = Modifier.fillMaxSize()) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth().systemBarsPadding().padding(12.dp),
            ) {
                IconButton(onClick = onBack) {
                    Icon(Icons.Filled.ArrowBack, contentDescription = "Back", tint = MaterialTheme.colorScheme.onBackground)
                }
                Spacer(Modifier.width(4.dp))
                Text("Call Logs", style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onBackground)
            }

            when {
                errorText != null -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(errorText!!, color = MaterialTheme.colorScheme.error)
                }
                logs == null -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
                }
                logs!!.isEmpty() -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("No calls yet", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                else -> LazyColumn(
                    modifier = Modifier.fillMaxSize().padding(horizontal = 14.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp),
                ) {
                    items(logs!!) { entry -> CallLogRow(entry) }
                }
            }
        }
    }
}

private enum class VerifyStatus { VERIFIED, NOT_VERIFIED, PENDING }

private fun verifyStatusOf(state: String): VerifyStatus = when (state) {
    "CONNECTED", "ENDED" -> VerifyStatus.VERIFIED
    "FAILED_VERIFICATION" -> VerifyStatus.NOT_VERIFIED
    else -> VerifyStatus.PENDING // RINGING (no answer), VERIFYING, DECLINED
}

@Composable
private fun CallLogRow(entry: CallLogEntry) {
    val status = verifyStatusOf(entry.state)
    val accentColor = when (status) {
        VerifyStatus.VERIFIED -> androidx.compose.ui.graphics.Color(0xFF2E7D32)
        VerifyStatus.NOT_VERIFIED -> MaterialTheme.colorScheme.error
        VerifyStatus.PENDING -> MaterialTheme.colorScheme.onSurfaceVariant
    }

    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f))
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Box(
            modifier = Modifier
                .size(42.dp)
                .clip(CircleShape)
                .background(accentColor.copy(alpha = 0.15f)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                if (entry.direction == "outgoing") Icons.Filled.CallMade else Icons.Filled.CallReceived,
                contentDescription = null,
                tint = accentColor,
                modifier = Modifier.size(20.dp),
            )
        }
        Spacer(Modifier.width(14.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                entry.other_party_name.ifBlank { entry.other_party_phone },
                color = MaterialTheme.colorScheme.onBackground,
                fontWeight = FontWeight.Medium,
                fontSize = 16.sp,
            )
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                VerifyBadge(status = status, callState = entry.state)
                Spacer(Modifier.width(8.dp))
                Text(formatTimestamp(entry.created_at), color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun VerifyBadge(status: VerifyStatus, callState: String) {
    val (bg, fg, icon, label) = when (status) {
        VerifyStatus.VERIFIED -> Quad(
            androidx.compose.ui.graphics.Color(0xFF2E7D32).copy(alpha = 0.15f),
            androidx.compose.ui.graphics.Color(0xFF2E7D32),
            Icons.Filled.CheckCircle,
            "Verified",
        )
        VerifyStatus.NOT_VERIFIED -> Quad(
            MaterialTheme.colorScheme.error.copy(alpha = 0.15f),
            MaterialTheme.colorScheme.error,
            Icons.Filled.Warning,
            "Likely Fraud",
        )
        VerifyStatus.PENDING -> Quad(
            MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.12f),
            MaterialTheme.colorScheme.onSurfaceVariant,
            pendingIcon(callState),
            pendingLabel(callState),
        )
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(bg)
            .padding(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Icon(icon, contentDescription = null, tint = fg, modifier = Modifier.size(12.dp))
        Spacer(Modifier.width(5.dp))
        Text(label, color = fg, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
    }
}

private fun pendingIcon(state: String) = when (state) {
    "DECLINED" -> Icons.Filled.PersonOff
    "RINGING" -> Icons.Filled.PersonOff
    else -> Icons.Filled.HourglassTop
}

private fun pendingLabel(state: String): String = when (state) {
    "DECLINED" -> "Declined"
    "RINGING" -> "No answer"
    "VERIFYING" -> "Verifying"
    else -> state
}

private data class Quad(
    val bg: androidx.compose.ui.graphics.Color,
    val fg: androidx.compose.ui.graphics.Color,
    val icon: androidx.compose.ui.graphics.vector.ImageVector,
    val label: String,
)

private fun formatTimestamp(iso: String): String = try {
    val instant = Instant.parse(iso)
    val zoned = instant.atZone(ZoneId.systemDefault())
    zoned.format(DateTimeFormatter.ofPattern("MMM d, h:mm a"))
} catch (e: Exception) {
    iso
}
