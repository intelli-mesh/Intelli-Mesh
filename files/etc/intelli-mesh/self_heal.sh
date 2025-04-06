#!/bin/sh

# Configuration
PRIMARY_INTERFACE="wan"
BACKUP_GATEWAY="192.168.10.102"
BACKUP_INTERFACE="br-default"
PING_TARGET="1.1.1.1"
LOG_FILE="/tmp/self_heal.log"

# Initialize log file
echo "==== $(date) - Network Monitor Started ====" > "$LOG_FILE"

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

log "Starting network monitoring on $PRIMARY_INTERFACE"

while true; do
    # Check primary interface
    if ping -I $PRIMARY_INTERFACE -c 2 -W 3 $PING_TARGET > /dev/null 2>&1; then
        log "Primary connection ($PRIMARY_INTERFACE) is ACTIVE"
        
        # Remove backup route if active
        if route -n | grep -q "$BACKUP_GATEWAY"; then
            log "Removing backup route"
            route del default gw $BACKUP_GATEWAY $BACKUP_INTERFACE
            log "Backup route removed successfully"
        fi
        
    else
        log "Primary connection ($PRIMARY_INTERFACE) is DOWN"
        
        # Add backup route if not already added
        if ! route -n | grep -q "$BACKUP_GATEWAY"; then
            log "Adding backup route"
            route add default gw $BACKUP_GATEWAY $BACKUP_INTERFACE
            log "Backup route added. Testing backup connection..."
            
            # Verify backup route
            if ping -I $BACKUP_INTERFACE -c 2 -W 3 $PING_TARGET > /dev/null 2>&1; then
                log "Backup route ($BACKUP_INTERFACE) is ACTIVE"
            else
                log "Backup route ($BACKUP_INTERFACE) FAILED to connect"
            fi
        else
            log "Backup route already active - maintaining connection"
        fi
    fi
    
    # Wait before next check
    sleep 10
done
